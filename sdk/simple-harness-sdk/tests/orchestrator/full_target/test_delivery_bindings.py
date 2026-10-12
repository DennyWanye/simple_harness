# SPDX-License-Identifier: Apache-2.0
"""2026-10-11 第三批：冻结清单里的接力文件（编程测评运行合规评估偏离 6）。

此前清单只列端口文件、只给端口文件发见证；数据边接上的上游交出的其余文件（它验收通过的全部文件、
接续上游收到的文件）只冻结在派发意图里，没有见证、没有新鲜度核对，交接时只按路径与哈希复核。
现在交付绑定进清单：每一份都带真实产出者那次验收的见证；版本证明不了报冲突（交规划器）；交接、
恢复、共享都读同一份清单；冻结输入第 1、2 版退役，具名拒绝。

**改坏检验**见各用例的说明。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent_orchestrator.artifacts.input_bindings import (
    DeliveredFile,
    DeliveryBinding,
    DeliverySources,
    InputManifest,
    ResolutionPolicy,
    ResolutionProblemKind,
    ResolvedInputBinding,
    ResourceIdentity,
    TargetRules,
    delivery_binding_id,
    materialise_plan,
    resolve_deliveries,
)
from agent_orchestrator.artifacts.taskgraph_inputs import decode_frozen_manifest
from agent_orchestrator.artifacts.versioning import UpstreamInput, manifest_upstream_inputs
from agent_orchestrator.contracts.evidence_state import (
    Availability,
    TruthValue,
    Validity,
    ValidityWitness,
    WitnessDecision,
    WitnessPurpose,
)
from agent_orchestrator.contracts.htn import BoundInput, OccurrenceId, TaskRef
from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.contracts.semantic_base import TypedRef, TypedRefKind, VersionedRef

SCHEMA = VersionedRef("desktop.workspace-outputs", 1, "a" * 64)
C = TaskRef("t-c")
RULES = TargetRules(namespace="workspace:t-c")
H = {name: digit * 64 for name, digit in (("a1", "1"), ("a2", "2"), ("b1", "3"), ("b2", "4"), ("x", "5"))}


def _witness(consumer: str, acceptance_id: str, *, support: int = 1, epoch: int = 1,
             usable: bool = True) -> ValidityWitness:
    return ValidityWitness(
        witness_id=f"w-{consumer}-{acceptance_id}-{epoch}",
        consumer_ref=TypedRef(kind=TypedRefKind.TASK, id=consumer, revision=1, content_hash="e" * 64),
        purpose=WitnessPurpose.START, truth=TruthValue.TRUE if usable else TruthValue.FALSE,
        freshness=Validity.CURRENT if usable else Validity.REVOKED, availability=Availability.READABLE,
        decision=WitnessDecision.USABLE if usable else WitnessDecision.BLOCKED,
        scope_id="mission", scope_epoch=epoch, support_revision=support, as_of_ms=1_000,
        support_refs=(TypedRef(kind=TypedRefKind.ACCEPTANCE, id=acceptance_id, revision=1,
                               content_hash="f" * 64),),
    )


def _policy(epoch: int = 1) -> ResolutionPolicy:
    return ResolutionPolicy(scope_epochs={"mission": epoch}, now_ms=2_000)


def _port(producer: str, acceptance: str, artifact: str, path: str, digest: str, *,
          witness: str | None = "w", consumer: TaskRef = C) -> ResolvedInputBinding:
    return ResolvedInputBinding(
        binding_id=f"binding-{artifact}", bound=BoundInput(
            requirement_id=f"req-{producer}", producer_result_id=f"r-{producer}", acceptance_id=acceptance,
            artifact_id=artifact, content_hash=digest, schema_ref=SCHEMA, source_revision="1"),
        producer_task_ref=TaskRef(producer), producer_occurrence=OccurrenceId(f"occ-{producer}"),
        output_port="delivery", support_revision=1, consumer_task_ref=consumer, input_port="delivery",
        port_ordinal=0, source_identity=ResourceIdentity(namespace=f"workspace:{producer}", path=path),
        produced_schema_ref=SCHEMA, read_policy="p", freshness_policy="f", disclosure_scope="mission",
        witness_id=witness,
    )


def _file(producer: str, acceptance: str, artifact: str, path: str, digest: str, *,
          carried_from: str | None = None, support: int = 1) -> DeliveredFile:
    return DeliveredFile(TaskRef(producer), acceptance, support, artifact, path, digest,
                         carried_from=None if carried_from is None else TaskRef(carried_from))


def _manifest(*ports: ResolvedInputBinding) -> InputManifest:
    return InputManifest(consumer_task_ref=C, bindings=ports)


# ---------------------------------------------------------------- resolve_deliveries


def test_every_accepted_file_of_a_data_bound_producer_is_delivered_with_its_licence():
    """a 的端口文件是 a.md；它那次验收还通过了 mod.py：mod.py 进清单，带 c 对 acc-a 的见证。

    **Mutation**: ``resolve_deliveries`` 不写 ``witness_id`` → red."""
    manifest = _manifest(_port("t-a", "acc-a", "art-a-md", "a.md", H["a1"]))
    sources = DeliverySources(files=(_file("t-a", "acc-a", "art-a-md", "a.md", H["a1"]),
                                     _file("t-a", "acc-a", "art-mod", "mod.py", H["a2"])),
                              received={"acc-a": frozenset()})
    result = resolve_deliveries(manifest, sources, target_rules=RULES,
                                witnesses={"acc-a": _witness("t-c", "acc-a")}, policy=_policy())
    assert result.ok, result.problems
    [delivery] = result.manifest.deliveries
    assert (delivery.path, delivery.artifact_id, delivery.acceptance_id, delivery.witness_id,
            delivery.carried_from) == ("mod.py", "art-mod", "acc-a", "w-t-c-acc-a-1", None)
    assert delivery.binding_id == delivery_binding_id("t-c", "art-mod", "acc-a")
    # 端口文件本身不重复成交付绑定；物化计划与意图输入都列出两份文件、每份带验收与见证
    plan = materialise_plan(result.manifest, RULES)
    assert sorted(entry.target.path for entry in plan.entries) == ["a.md", "mod.py"]
    inputs = manifest_upstream_inputs(result.manifest, RULES)
    assert [(i.path, i.acceptance_id, i.witness_id, i.carried_from) for i in inputs] == [
        ("a.md", "acc-a", "w", None), ("mod.py", "acc-a", "w-t-c-acc-a-1", None)]


def test_a_delivered_file_without_a_usable_licence_is_refused_by_name_not_dropped():
    """接力文件的真实产出者验收被撤销（见证 BLOCKED）或根本没见证：不悄悄少交，报问题点名文件与验收。

    **Mutation**: 交付见证不可用时 ``continue`` 而不记 problem → red."""
    manifest = _manifest(_port("t-b", "acc-b", "art-b-md", "b.md", H["b1"]))
    sources = DeliverySources(
        files=(_file("t-b", "acc-b", "art-b-md", "b.md", H["b1"]),
               _file("t-a", "acc-a", "art-a-md", "a.md", H["a1"], carried_from="t-b")),
        received={"acc-b": frozenset({("t-a", "a.md", H["a1"])})})
    blocked = resolve_deliveries(manifest, sources, target_rules=RULES, policy=_policy(),
                                 witnesses={"acc-b": _witness("t-c", "acc-b"),
                                            "acc-a": _witness("t-c", "acc-a", usable=False)})
    assert blocked.manifest is None
    [problem] = blocked.problems
    assert problem.kind is ResolutionProblemKind.DELIVERY_WITNESS_NOT_USABLE
    assert "a.md" in problem.detail and "acc-a" in problem.detail and "relayed by t-b" in problem.detail
    assert problem.candidates == ("art-a-md",) and problem.producers == ("t-a",)
    missing = resolve_deliveries(manifest, sources, target_rules=RULES, policy=_policy(),
                                 witnesses={"acc-b": _witness("t-c", "acc-b")})
    assert [p.kind for p in missing.problems] == [ResolutionProblemKind.DELIVERY_WITNESS_MISSING]
    # 纪元翻了：旧见证不再新鲜，同样拒绝
    stale = resolve_deliveries(manifest, sources, target_rules=RULES, policy=_policy(epoch=2),
                               witnesses={"acc-b": _witness("t-c", "acc-b", epoch=2),
                                          "acc-a": _witness("t-c", "acc-a", epoch=1)})
    assert [p.kind for p in stale.problems] == [ResolutionProblemKind.DELIVERY_WITNESS_NOT_USABLE]


def test_a_relay_takes_the_proven_next_version_and_an_unproven_pair_is_a_conflict():
    """a 写 shared（v1），b 接着 a 改写 shared（v2），c 接 a、b：b 的冻结清单证明它收到过 v1 → 取 v2。
    c 没收到过 a 的那份 → 版本冲突，候选与写出它们的步骤都点名。

    **Mutation**: ``others = []``（先到先占）→ red（MU-02）。"""
    manifest = _manifest(_port("t-a", "acc-a", "art-a-md", "a.md", H["a1"]),
                         _port("t-b", "acc-b", "art-b-md", "b.md", H["b1"]))
    witnesses = {"acc-a": _witness("t-c", "acc-a"), "acc-b": _witness("t-c", "acc-b")}
    relayed = DeliverySources(
        files=(_file("t-a", "acc-a", "art-a-md", "a.md", H["a1"]),
               _file("t-a", "acc-a", "art-sh1", "shared.md", H["a2"]),
               _file("t-b", "acc-b", "art-b-md", "b.md", H["b1"]),
               _file("t-b", "acc-b", "art-sh2", "shared.md", H["b2"]),
               _file("t-a", "acc-a", "art-sh1", "shared.md", H["a2"], carried_from="t-b")),
        received={"acc-a": frozenset(), "acc-b": frozenset({("t-a", "shared.md", H["a2"])})})
    result = resolve_deliveries(manifest, relayed, target_rules=RULES, witnesses=witnesses, policy=_policy())
    assert result.ok, result.problems
    shared = {d.path: d for d in result.manifest.deliveries}["shared.md"]
    assert (shared.artifact_id, str(shared.producer_task_ref)) == ("art-sh2", "t-b")
    unproven = DeliverySources(
        files=(_file("t-a", "acc-a", "art-a-md", "a.md", H["a1"]),
               _file("t-a", "acc-a", "art-sh1", "shared.md", H["a2"]),
               _file("t-b", "acc-b", "art-b-md", "b.md", H["b1"]),
               _file("t-b", "acc-b", "art-sh2", "shared.md", H["b2"])),
        received={"acc-a": frozenset(), "acc-b": frozenset()})
    conflict = resolve_deliveries(manifest, unproven, target_rules=RULES, witnesses=witnesses, policy=_policy())
    [problem] = conflict.problems
    assert problem.kind is ResolutionProblemKind.DELIVERY_VERSION_CONFLICT
    assert problem.candidates == ("art-sh1", "art-sh2") and problem.producers == ("t-a", "t-b")


def test_a_port_document_wins_only_over_versions_its_producer_received():
    """端口文件就是计划点名的那份：别的来源在同一路径交了不同内容——是端口产出者收到过、接着改的旧版
    就不算另一份（单链接力）；不是，则冲突（MU-05 / MU-06）。"""
    manifest = _manifest(_port("t-b", "acc-b", "art-b-amd", "a.md", H["b1"]))
    witnesses = {"acc-b": _witness("t-c", "acc-b"), "acc-a": _witness("t-c", "acc-a")}
    plain_relay = DeliverySources(
        files=(_file("t-b", "acc-b", "art-b-amd", "a.md", H["b1"]),
               _file("t-a", "acc-a", "art-a-amd", "a.md", H["a1"], carried_from="t-b")),
        received={"acc-b": frozenset({("t-a", "a.md", H["a1"])})})
    result = resolve_deliveries(manifest, plain_relay, target_rules=RULES, witnesses=witnesses, policy=_policy())
    assert result.ok and result.manifest.deliveries == ()
    assert [i.artifact_id for i in manifest_upstream_inputs(result.manifest, RULES)] == ["art-b-amd"]
    stranger = DeliverySources(
        files=(_file("t-b", "acc-b", "art-b-amd", "a.md", H["b1"]),
               _file("t-a", "acc-a", "art-a-amd", "a.md", H["a1"], carried_from="t-b")),
        received={"acc-b": frozenset()})
    conflict = resolve_deliveries(manifest, stranger, target_rules=RULES, witnesses=witnesses, policy=_policy())
    [problem] = conflict.problems
    assert problem.kind is ResolutionProblemKind.DELIVERY_VERSION_CONFLICT
    assert set(problem.candidates) == {"art-b-amd", "art-a-amd"} and set(problem.producers) == {"t-b", "t-a"}


def test_one_file_reaching_the_consumer_twice_is_one_binding_and_unreadable_sources_are_named():
    """同一份文件（同产物、同验收）经直接与接力两条路到达：只记一条。读不到的来源点名报，不静默少交。

    **Mutation**: ``by_path.setdefault(...).setdefault(...)`` 改成覆盖 → 仍一条（内容相同），但
    ``unavailable`` 不报 → red。"""
    manifest = _manifest(_port("t-a", "acc-a", "art-a-md", "a.md", H["a1"]),
                         _port("t-b", "acc-b", "art-b-md", "b.md", H["b1"]))
    sources = DeliverySources(
        files=(_file("t-a", "acc-a", "art-x", "x.py", H["x"]),
               _file("t-a", "acc-a", "art-x", "x.py", H["x"], carried_from="t-b"),
               _file("t-b", "acc-b", "art-b-md", "b.md", H["b1"]),
               _file("t-a", "acc-a", "art-a-md", "a.md", H["a1"])),
        received={"acc-a": frozenset(), "acc-b": frozenset({("t-a", "x.py", H["x"])})})
    witnesses = {"acc-a": _witness("t-c", "acc-a"), "acc-b": _witness("t-c", "acc-b")}
    result = resolve_deliveries(manifest, sources, target_rules=RULES, witnesses=witnesses, policy=_policy())
    assert result.ok and [(d.path, d.carried_from) for d in result.manifest.deliveries] == [("x.py", None)]
    broken = DeliverySources(files=sources.files, received=sources.received,
                             unavailable=(("acc-b", "accepted artifact art-gone is missing or changed"),))
    refused = resolve_deliveries(manifest, broken, target_rules=RULES, witnesses=witnesses, policy=_policy())
    assert [p.kind for p in refused.problems] == [ResolutionProblemKind.DELIVERY_SOURCE_UNAVAILABLE]
    assert "acc-b" in refused.problems[0].detail


# ---------------------------------------------------------------- frozen record


def test_the_frozen_manifest_round_trips_deliveries_and_refuses_the_old_shape():
    """冻结清单的编解码带交付绑定；没有 ``deliveries`` 的旧清单（第 2 版世界）不再解码。

    **Mutation**: ``decode_frozen_manifest`` 的键集合去掉 ``deliveries`` → red。"""
    manifest = InputManifest(
        consumer_task_ref=C, bindings=(_port("t-a", "acc-a", "art-a-md", "a.md", H["a1"]),),
        deliveries=(DeliveryBinding(binding_id=delivery_binding_id("t-c", "art-x", "acc-a"),
                                    producer_task_ref=TaskRef("t-a"), acceptance_id="acc-a", support_revision=1,
                                    artifact_id="art-x", path="x.py", content_hash=H["x"], witness_id="w-x",
                                    carried_from=TaskRef("t-b")),))
    document = manifest.to_json()
    assert set(document) == {"consumer_task_ref", "bindings", "deliveries", "pending", "manifest_hash"}
    decoded = decode_frozen_manifest(document)
    assert decoded == manifest and decoded.manifest_hash() == manifest.manifest_hash()
    assert decoded.deliveries[0].carried_from == TaskRef("t-b")
    old = {key: value for key, value in document.items() if key != "deliveries"}
    with pytest.raises(ContractError):
        decode_frozen_manifest(old)
    # 交付绑定进哈希：同端口、不同交付 ≠ 同一份输入
    bare = InputManifest(consumer_task_ref=C, bindings=manifest.bindings)
    assert bare.manifest_hash() != manifest.manifest_hash()
    with pytest.raises(ContractError, match="one delivered version per path"):
        InputManifest(consumer_task_ref=C, deliveries=(
            manifest.deliveries[0],
            DeliveryBinding(binding_id="delivery-other", producer_task_ref=TaskRef("t-b"), acceptance_id="acc-b",
                            support_revision=1, artifact_id="art-y", path="x.py", content_hash=H["b1"])))


def test_the_frozen_input_record_carries_acceptance_licence_and_relay_and_refuses_the_old_shape():
    """派发意图里每条输入都带验收、见证与接力来源；四字段的旧形状以具名码拒绝。"""
    item = UpstreamInput("t-a", "x.py", H["x"], "art-x", "acc-a", "w-x", "t-b")
    assert UpstreamInput.from_json(item.to_json()) == item
    with pytest.raises(ContractError, match="TASKGRAPH_FROZEN_INPUT_VERSION_RETIRED"):
        UpstreamInput.from_json({"task_id": "t-a", "path": "x.py", "content_hash": H["x"], "artifact_id": "art-x"})


def test_a_retired_frozen_input_version_is_refused_by_name():
    """第 1、2 版派发意图：不是"损坏"，是退役——具名拒绝，不复核、不回落。

    **Mutation**: ``_frozen_binding`` 接受 ``version in (2, 3)`` → red。"""
    from agent_orchestrator.contracts.models import sha256_hex
    from agent_orchestrator.orchestrator.taskgraph_dispatch import (
        FROZEN_INPUT_VERSION,
        _intent_inputs,
        _require_materialization_receipt,
    )
    from agent_orchestrator.storage.store import StoreError

    message = {"text": "x"}
    for version in (1, 2):
        frozen = {"version": version, "source_revision": 1, "manifest_hash": "m" * 64, "target_rules": None}
        if version == 2:
            frozen["data_inputs"] = []
        intent = SimpleNamespace(config={"taskgraph_inputs": frozen, "inputs": [], "message": message},
                                 input_hash=sha256_hex(message), subject_id="t-c:attempt-1")
        with pytest.raises(StoreError, match="TASKGRAPH_FROZEN_INPUT_VERSION_RETIRED"):
            _intent_inputs(intent, InputManifest(consumer_task_ref=C), source_revision=1, manifest_hash="m" * 64)
        with pytest.raises(StoreError, match="TASKGRAPH_FROZEN_INPUT_VERSION_RETIRED"):
            _require_materialization_receipt(None, intent)
    assert FROZEN_INPUT_VERSION == 3
    current = SimpleNamespace(config={"taskgraph_inputs": {
        "version": 3, "source_revision": 1, "manifest_hash": "m" * 64,
        "target_rules": {"namespace": "workspace:t-c", "port_prefixes": {}, "preserve_source_namespace": False,
                         "case_insensitive": False}},
        "inputs": [], "message": message}, input_hash=sha256_hex(message))
    assert _intent_inputs(current, InputManifest(consumer_task_ref=C), source_revision=1, manifest_hash="m" * 64) == ()


# ---------------------------------------------------------------- handover & sharing


def test_handover_rechecks_every_delivery_licence_and_sharing_tells_different_deliveries_apart(monkeypatch):
    """交接核对：交付绑定的见证不在当前纪元 → 拒绝（§8.2 不取 latest）。共享判定：端口相同、交付不同的
    两份清单摘要不同。

    **Mutation**: ``require_current_manifest_use`` 不核交付见证 → 第一段 red；共享摘要只算 bindings →
    第二段 red。"""
    from agent_orchestrator.artifacts import taskgraph_inputs as ti
    from agent_orchestrator.contracts.models import sha256_hex

    delivered = InputManifest(
        consumer_task_ref=C, bindings=(),
        deliveries=(DeliveryBinding(binding_id=delivery_binding_id("t-c", "art-x", "acc-a"),
                                    producer_task_ref=TaskRef("t-a"), acceptance_id="acc-a", support_revision=1,
                                    artifact_id="art-x", path="x.py", content_hash=H["x"], witness_id="w-x"),))
    network = SimpleNamespace(occurrences=(SimpleNamespace(task_id=C, occurrence_id=OccurrenceId("occ-c")),),
                              data_requirements=(), binding_for_occurrence=lambda occurrence: SimpleNamespace(input_ports=()))
    policy = ResolutionPolicy(scope_epochs={"mission": 2}, now_ms=2_000)
    accepted = SimpleNamespace(at=lambda occurrence, port: ())
    ti.require_current_manifest_use(delivered, network, accepted=accepted, policy=policy,
                                    witnesses={"acc-a": _witness("t-c", "acc-a", epoch=2)})
    with pytest.raises(ContractError, match="TASKGRAPH_FROZEN_DELIVERY_WITNESS_NOT_CURRENT"):
        ti.require_current_manifest_use(delivered, network, accepted=accepted, policy=policy,
                                        witnesses={"acc-a": _witness("t-c", "acc-a", epoch=1)})
    with pytest.raises(ContractError, match="TASKGRAPH_FROZEN_DELIVERY_WITNESS_NOT_CURRENT"):
        ti.require_current_manifest_use(delivered, network, accepted=accepted, policy=policy, witnesses={})

    from agent_orchestrator.orchestrator import taskgraph_sharing_inputs as sharing
    bare = InputManifest(consumer_task_ref=C)
    digests = []
    for manifest in (bare, delivered):
        monkeypatch.setattr(sharing, "require_current_manifest_use", lambda *a, **k: None)
        store = SimpleNamespace()
        semantics = SimpleNamespace(get_input_manifest=lambda digest, m=manifest: m.to_json())
        monkeypatch.setattr(sharing, "HtnStore", lambda store: semantics)
        acceptance = SimpleNamespace(input_manifest_hash=sha256_hex(manifest.to_json()), acceptance_id="acc-c")
        entry = SimpleNamespace(occurrence_id=OccurrenceId("occ-c"), task_id=C)
        local = SimpleNamespace(view=SimpleNamespace(network=SimpleNamespace(
            mission_id="m", binding_for_occurrence=lambda o: SimpleNamespace(
                contract_revision=1, input_binding_revision=1, dispatch_generation=1)), licences={}),
            accepted_outputs=SimpleNamespace(value=None), source_input_policy=SimpleNamespace(value=None))
        proof = sharing.read_shared_inputs(SimpleNamespace(store=store), local, entry, acceptance)
        digests.append(proof.semantic_inputs_hash)
    assert digests[0] != digests[1]
