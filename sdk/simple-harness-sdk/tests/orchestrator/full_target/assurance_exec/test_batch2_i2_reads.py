# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""第 2 批车道 I2：精确引用读取器（``storage/assurance_reads.py``）。

* A08 当前性失败码 ``SOURCE_NOT_CURRENT``：引用的身份还在、但钉住的版本不是来源现在持有的那一版，
  按资产 ``ref-resolution-map.json`` 用这个名字拒绝，不再混进 ``SOURCE_UNAVAILABLE`` /
  ``REF_REVISION_MISMATCH``。
* A09 回执解析核写者与回执种类：``commit_receipt`` 由真实 Commit writer 写，行上的 ``kind``
  就是写者身份、``subject_id`` 是它写的对象；调用方说出期望的，不符按名拒绝。
* A10 解析器返回计划合同形状 ``ResolvedRef(body, pin, tenant, mission, issuer, state_witness)``；
  带现行授权建的读取器在解析时就核用途与访问（复用 ``_permission``），不再由使用点另核。
* A13 完成范围与完成规格经原 OCC 读者（``OperationCompletionStore``）读：哈希对得上但 OCC
  读者读不回来的行，按 ``REF_BODY_CONFLICT`` 拒绝，不再直接读表放行。
"""

from __future__ import annotations

import pytest

from agent_orchestrator.assurance.certificates import UseIdentity
from agent_orchestrator.assurance.codec import AssuranceError, canonical, decode, fingerprint
from agent_orchestrator.assurance.evidence import ReadItem
from agent_orchestrator.assurance.refs import AssuranceRef, Pin
from agent_orchestrator.assurance.root_gate import CurrentReadPermission
from agent_orchestrator.contracts.models import Budget, Mission, MissionStatus
from agent_orchestrator.storage.assurance_reads import AssuranceReader, ResolvedRef
from agent_orchestrator.storage.store import Store

TENANT, MISSION, HASH = "tenant-i2", "mission-i2", "a" * 64


@pytest.fixture
def store(tmp_path):
    store = Store.open(tmp_path / "orchestrator.db")
    store.insert_mission(
        Mission(MISSION, "goal", ("c",), (), (), "low", Budget(), TENANT, MissionStatus.CREATED, 1.0, 1, "key-i2"),
        spec_hash="b" * 64,
    )
    yield store
    store.close()


def _refused(call, code):
    with pytest.raises(AssuranceError) as raised:
        call()
    assert raised.value.code == code, raised.value
    return raised.value


def _requirements(store, revision=1):
    body = {"mission_id": MISSION, "revision": revision, "text": "要求"}
    store.connection.execute(
        "INSERT INTO requirements_revisions(mission_id,revision,revision_id,content_hash,authority_subject,"
        "revision_json,created_at) VALUES (?,?,?,?,?,?,?)",
        (MISSION, revision, f"req-{revision}", fingerprint(body), None, canonical(body), 1.0),
    )
    return AssuranceRef("requirements", Pin(f"req-{revision}", revision, fingerprint(body)))


def _source(store, revision=1):
    store.connection.execute(
        "INSERT INTO sources(mission_id,tenant_id,path,version_hash,kind,trust,registered_at,superseded_by,"
        "revoked,revision) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (MISSION, TENANT, "sources/spec.md", HASH, "markdown", "untrusted_external", 1.0, None, 0, revision),
    )
    return AssuranceRef("source", Pin("sources/spec.md", revision, HASH))


def _receipt(store, receipt_id="assurance-review-prepared:rk-1", kind="AssuranceReviewPrepared", subject="rk-1"):
    body = {"mission_id": MISSION, "review_key": subject, "request_command_id": "cmd-1"}
    store.insert_receipt(commit_id=receipt_id, kind=kind, subject_id=subject, base_version=0,
                         proposal_hash=fingerprint(body), receipt=body)
    return AssuranceRef("commit_receipt", Pin(receipt_id, 0, fingerprint(body)))


# --------------------------------------------------------------------------- A08
def test_a08_a_pinned_revision_the_source_no_longer_holds_is_not_current(store):
    """同一个身份存在、钉的版本不是它现在的版本 → SOURCE_NOT_CURRENT；身份根本不存在仍是
    SOURCE_UNAVAILABLE；不可变种类带了版本号仍是 REF_REVISION_MISMATCH（那是引用写错，不是当前性）。

    **改坏检验**：读取器找不到 (id, revision) 时不再探身份是否存在、一律报 SOURCE_UNAVAILABLE → 变红。"""
    reader = AssuranceReader(store, tenant_id=TENANT, mission_id=MISSION)
    good = _requirements(store, 1)
    assert decode(reader.read_exact_metadata(good).body_json)["revision"] == 1
    stale = AssuranceRef("requirements", Pin(good.pin.id, 2, good.pin.content_hash))
    assert _refused(lambda: reader.read_exact_metadata(stale), "SOURCE_NOT_CURRENT").locator == good.pin.id
    _refused(lambda: reader.read_exact_metadata(AssuranceRef("requirements", Pin("req-none", 1, HASH))),
             "SOURCE_UNAVAILABLE")
    _refused(lambda: reader.read_exact_metadata(AssuranceRef("review", Pin("rec-x", 1, HASH))),
             "REF_REVISION_MISMATCH")


def test_a08_a_blob_whose_lifecycle_revision_moved_on_is_not_current(store):
    """资料按 path+version_hash 找得到、生命周期版本号已不是钉住的那个 → SOURCE_NOT_CURRENT。"""
    reader = AssuranceReader(store, tenant_id=TENANT, mission_id=MISSION)
    current = _source(store, revision=2)
    assert reader.read_exact_metadata(current).ref == current
    _refused(lambda: reader.read_exact_metadata(AssuranceRef("source", Pin(current.pin.id, 1, HASH))),
             "SOURCE_NOT_CURRENT")


# --------------------------------------------------------------------------- A09
def test_a09_a_commit_receipt_is_checked_against_its_writer_and_kind(store):
    """调用方说期望的回执种类（写者）与对象：行上的 kind / subject_id 不符各按名拒绝；相符时
    ``issuer`` 就是写者的回执种类。没说期望的照常解析（通用重查不知道种类）。

    **改坏检验**：解析器忽略 ``receipt_kind`` → 第一处 _refused 变红。"""
    reader = AssuranceReader(store, tenant_id=TENANT, mission_id=MISSION)
    ref = _receipt(store)
    resolved = reader.read_exact_metadata(ref, receipt_kind="AssuranceReviewPrepared", receipt_subject="rk-1")
    assert resolved.issuer == "AssuranceReviewPrepared"
    assert reader.read_exact_metadata(ref) == resolved
    _refused(lambda: reader.read_exact_metadata(ref, receipt_kind="AssuranceReviewImported"), "REF_ISSUER_MISMATCH")
    _refused(lambda: reader.read_exact_metadata(ref, receipt_kind="AssuranceReviewPrepared", receipt_subject="rk-2"),
             "REF_SUBJECT_MISMATCH")
    # 别的任务的回执：归属仍先于种类
    other = {"mission_id": "mission-other", "review_key": "rk-9"}
    store.insert_receipt(commit_id="r-other", kind="AssuranceReviewPrepared", subject_id="rk-9", base_version=0,
                         proposal_hash=fingerprint(other), receipt=other)
    _refused(lambda: reader.read_exact_metadata(AssuranceRef("commit_receipt", Pin("r-other", 0, fingerprint(other))),
                                                receipt_kind="AssuranceReviewPrepared"), "REF_SCOPE_MISMATCH")


# --------------------------------------------------------------------------- A10
def _permission_for(ref: AssuranceRef, *, not_after_ms: int = 2_000_000_000_000) -> CurrentReadPermission:
    return CurrentReadPermission(
        ReadItem("ACCESS", "access:" + ref.pin.id, fingerprint({"ref": ref.to_json()})),
        ReadItem("POLICY", "policy:fixed", HASH),
        not_after_ms,
    )


def _identity() -> UseIdentity:
    return UseIdentity(MISSION, "REVIEW", "rk-1", "scope-1", "principal-1", "DISCLOSE", "root-1")


def test_a10_the_resolver_returns_the_contract_shape_and_checks_access_when_built_with_authority(store):
    """ResolvedRef 带 tenant/mission/issuer/state_witness；带授权建的读取器解析时就核访问：
    授权拒绝 → 解析失败（不凭正文放行）；授权过期 → CHECK_USE_EXPIRED；通过 → ``permission`` 随结果返回，
    且相等比较不看许可租期（重查时许可是按"现在"重发的）。

    **改坏检验**：解析器不调 ``_permission`` → 下面的 ACCESS_DENIED 一处变红。"""
    good = _requirements(store, 1)
    plain = AssuranceReader(store, tenant_id=TENANT, mission_id=MISSION)
    resolved = plain.read_exact_metadata(good)
    assert isinstance(resolved, ResolvedRef)
    assert (resolved.tenant_id, resolved.mission_id, resolved.issuer) == (TENANT, MISSION, "requirements_revisions")
    assert "revision_json" not in decode(resolved.state_witness_json)
    assert resolved.permission is None

    def refusing(identity, ref):
        raise AssuranceError("ACCESS_DENIED", ref.pin.id)

    _refused(lambda: AssuranceReader(store, tenant_id=TENANT, mission_id=MISSION, authority=refusing,
                                     identity=_identity()).read_exact_metadata(good), "ACCESS_DENIED")
    _refused(lambda: AssuranceReader(store, tenant_id=TENANT, mission_id=MISSION,
                                     authority=lambda identity, ref: _permission_for(ref, not_after_ms=1),
                                     identity=_identity()).read_exact_metadata(good, now_ms=5), "CHECK_USE_EXPIRED")
    granted = AssuranceReader(store, tenant_id=TENANT, mission_id=MISSION,
                              authority=lambda identity, ref: _permission_for(ref), identity=_identity())
    first = granted.read_exact_metadata(good, now_ms=5)
    assert first.permission == _permission_for(good)
    assert first == resolved  # 许可不进相等比较
    # 授权与身份要一起给；身份的任务要和读取器一致
    _refused(lambda: AssuranceReader(store, tenant_id=TENANT, mission_id=MISSION, authority=refusing),
             "CURRENT_READ_AUTHORITY_REQUIRED")
    _refused(lambda: AssuranceReader(store, tenant_id=TENANT, mission_id=MISSION,
                                     authority=lambda identity, ref: _permission_for(ref),
                                     identity=UseIdentity("mission-other", "REVIEW", "rk", "s", "p", "DISCLOSE", "r")),
             "REF_SCOPE_MISMATCH")


# --------------------------------------------------------------------------- A13
def test_a13_completion_spec_and_scope_are_read_through_the_occ_reader(store):
    """一行 completion_spec，哈希与引用对得上，但 OCC 读者（合同解码 + 行身份校验）读不回来：
    直接读表会放行，经 OCC 读者按 REF_BODY_CONFLICT 拒绝。

    **改坏检验**：解析器把 completion_spec/completion_scope 退回直接读表 → 变红。"""
    reader = AssuranceReader(store, tenant_id=TENANT, mission_id=MISSION)
    requirements = _requirements(store, 1)
    body = {"not": "a completion spec"}
    store.insert_receipt(commit_id="spec-approval", kind="operation_completion_spec_approved", subject_id="spec-1",
                         base_version=0, proposal_hash=fingerprint(body), receipt={"mission_id": MISSION})
    store.connection.execute(
        "INSERT INTO operation_completion_specs(spec_id,mission_id,requirements_revision,requirements_hash,spec_hash,"
        "document_json,approval_receipt_id,created_at_ms) VALUES (?,?,?,?,?,?,?,?)",
        ("spec-1", MISSION, 1, requirements.pin.content_hash, fingerprint(body), canonical(body), "spec-approval", 1),
    )
    _refused(lambda: reader.read_exact_metadata(AssuranceRef("completion_spec", Pin("spec-1", 0, fingerprint(body)))),
             "REF_BODY_CONFLICT")
    _refused(lambda: reader.read_exact_metadata(AssuranceRef("completion_scope", Pin("scope-none", 0, HASH))),
             "SOURCE_UNAVAILABLE")
