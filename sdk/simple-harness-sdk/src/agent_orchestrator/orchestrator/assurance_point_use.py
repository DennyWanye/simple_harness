# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""时点使用证书（推后第 1 批 A26；原计划 ASSURANCE-EXEC-1.1 §8.4、附录 C.1；AER §9.4、§12.2）。

规划（PLAN）、开工交接（START）、装上下文（CONTEXT）、恢复在途尝试（RECOVERY）在**用证据的那一刻**
签一张使用证书：消费方报出这次用了哪些证据（证据声明），这里逐项用**原有的那个判定**核它此刻是否
当前——知识 :func:`knowledge_standing`、步骤摘要 :func:`step_summaries`、一步的开工地基（有效性见证
``is_fresh_for`` 与它们背后的验收 :func:`acceptance_is_current`）。这里不加任何语义判断。

全部当前：钉住精确对象（验收、观察、消费方自己的任务契约或现行要求），逐个向现行授权取 ACCESS /
POLICY 见证，到期取最早租期与见证期限，组一张 USABLE 证书，在消费方自己的写事务里过最终核对
（与 ACCEPT 同一个 :func:`check_certificate_binding`），写证书表、依赖索引与一条使用回执（回执里记
证据声明，供复核与恢复读）。有一项不当前：不签，返回具名原因（哪一项、为什么）。

披露（DISCLOSE，推后第 2 批 A03；附录 C.1 第 488、496 行）：原生下载、审阅员初始材料、审阅员取证读、
审阅员读黑板，交出前都过这里。证据是"精确对象"（``EvidenceClaim.exact``），判定就是原有的精确读者
``read_exact_metadata``（存在、哈希、归属），再取 ACCESS / POLICY；原生下载另核调用者就是部署的主体。

MAINTAIN（执行期间持续成立）要实际持续监测；产品没有，签发方直接挡住（§8.4：点状采样不冒充连续）。

时点证书在签发事务里就用掉了，之后是历史：不进有效性观察、不排到期唤醒
（:data:`~agent_orchestrator.assurance.certificates.POINT_PURPOSES`）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..assurance.certificates import POINT_PURPOSES, UseCertificate, UseIdentity, check_certificate_binding
from ..assurance.codec import AssuranceError, canonical, decode, fingerprint, integer, text
from ..assurance.evidence import ReadItem
from ..assurance.refs import AssuranceRef, Pin
from ..storage.assurance_reads import AssuranceReader, clock_discontinuous, read_epochs_locked
from ..storage.assurance_store import AssuranceStore
from ..storage.assurance_work import atomic
from .assurance_check_use import _merge_reads
from .assurance_validity import USE_CERTIFIED_KIND

ACTION_CONSUMER = "ACTION"
ATTEMPT_CONSUMER = "ATTEMPT"
PLANNING_REQUEST_CONSUMER = "PLANNING_REQUEST"
#: 运行中一次读取工具调用：执行者 / 审阅员的 ``knowledge_read``（裁决 2026-10-07 第 5 件）、审阅员取证读
TOOL_CALL_CONSUMER = "TOOL_CALL"
#: 一次发给审阅模型的请求（派发意图编号）：交出初始材料（A03）
REVIEW_INTENT_CONSUMER = "REVIEW_INTENT"
#: 本人在界面里读一个产物（A03 原生下载）
NATIVE_READ_CONSUMER = "NATIVE_READ"
READER_VERSION = "assurance-point-use-v1"
#: §8.4：需要 MAINTAIN 而没有实际持续监测 / 锁 / fence。
MAINTAIN_MONITOR_UNAVAILABLE = "MAINTAIN_MONITOR_UNAVAILABLE"
#: 没装保证通道的有效性组件，却要用证据：不签，也不放行。
USE_CERTIFICATE_REQUIRED = "USE_CERTIFICATE_REQUIRED"
CLAIM_KINDS = frozenset({"knowledge", "summary", "step_ground", "exact"})


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    """消费方这次用到的一项证据。``version``：知识是版本号，摘要是摘要原文的哈希，开工地基与精确对象
    没有（精确对象的 ``id`` 是它的引用原文 ``{kind, pin}``）。"""

    kind: str
    id: str
    version: int | str | None = None

    def __post_init__(self) -> None:
        if self.kind not in CLAIM_KINDS:
            raise AssuranceError("EVIDENCE_CLAIM_INVALID", str(self.kind))
        text(self.id)
        if self.kind == "knowledge":
            integer(self.version, minimum=1)
        elif self.kind == "summary":
            text(self.version)
        elif self.kind == "exact":
            AssuranceRef.from_json(decode(self.id))
            if self.version is not None:
                raise AssuranceError("EVIDENCE_CLAIM_INVALID", self.kind)
        elif self.version is not None:
            raise AssuranceError("EVIDENCE_CLAIM_INVALID", self.kind)

    @classmethod
    def knowledge(cls, knowledge_id: str, version: int) -> EvidenceClaim:
        return cls("knowledge", str(knowledge_id), int(version))

    @classmethod
    def summary(cls, row_id: str, summary_sha256: str) -> EvidenceClaim:
        return cls("summary", str(row_id), str(summary_sha256))

    @classmethod
    def step_ground(cls, task_id: str) -> EvidenceClaim:
        return cls("step_ground", str(task_id))

    @classmethod
    def exact(cls, ref: AssuranceRef) -> EvidenceClaim:
        return cls("exact", canonical(ref.to_json()))

    @property
    def ref(self) -> AssuranceRef:
        if self.kind != "exact":
            raise AssuranceError("EVIDENCE_CLAIM_INVALID", self.kind)
        return AssuranceRef.from_json(decode(self.id))

    @property
    def label(self) -> str:
        """拒绝原因里怎么称呼这一项。"""
        if self.kind == "knowledge":
            return f"knowledge:{self.id}@{self.version}"
        if self.kind == "exact":
            return f"{self.ref.kind}:{self.ref.pin.id}"
        return f"{self.kind}:{self.id}"

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "version": self.version}

    @classmethod
    def from_json(cls, value: object) -> EvidenceClaim:
        if not isinstance(value, Mapping) or set(value) != {"kind", "id", "version"}:
            raise AssuranceError("EVIDENCE_CLAIM_INVALID")
        return cls(value["kind"], value["id"], value["version"])


@dataclass(frozen=True, slots=True)
class PointUse:
    """一次时点使用的结论：可以用（``refusals`` 为空），或者不可以、原因逐项写明。"""

    purpose: str
    mission_id: str
    consumer_kind: str
    consumer_id: str
    claims: tuple[EvidenceClaim, ...]
    refusals: tuple[str, ...] = ()
    certificate_id: str | None = None
    certificate: UseCertificate | None = None

    @property
    def usable(self) -> bool:
        return not self.refusals


class PointUseRefused(AssuranceError):
    """时点使用被拒：``refusals`` 是具名原因，消费方如实停下或退回，不替谁判断。"""

    def __init__(self, use: PointUse) -> None:
        self.use = use
        super().__init__("USE_EVIDENCE_NOT_CURRENT", "; ".join(use.refusals)[:1000])


# ------------------------------------------------------------- 消费方报证据
def _unique(claims: Sequence[EvidenceClaim]) -> tuple[EvidenceClaim, ...]:
    return tuple(dict.fromkeys(claims))


def planning_claims(package: Mapping[str, Any]) -> tuple[EvidenceClaim, ...]:
    """规划包里当作事实给规划器看的：已验证知识与核对过的步骤摘要（候选结论是线索，不在内）。"""
    from ..context.knowledge_tools import _digest

    claims: list[EvidenceClaim] = []
    for row in (package.get("views") or {}).get("knowledge") or ():
        if row.get("layer") == "verified":
            claims.append(EvidenceClaim.knowledge(row["id"], row["version"]))
        elif row.get("layer") == "summary":
            claims.append(EvidenceClaim.summary(row["id"], _digest(str(row["summary"]))))
    return _unique(claims)


def context_claims(knowledge: Any, upstream: Sequence[Mapping[str, Any]]) -> tuple[EvidenceClaim, ...]:
    """推给执行者的：冻结的已验证知识、步骤摘要层、直接上游里核对过的摘要。"""
    from ..context.knowledge_tools import _digest

    claims = [EvidenceClaim.knowledge(item["id"], item["version"]) for item in knowledge.frozen_ids]
    claims.extend(EvidenceClaim.summary(row["id"], row["summary_sha256"]) for row in knowledge.step_summaries)
    for row in upstream:
        if row.get("summary_checked_by") and row.get("accepted_result_id"):
            claims.append(EvidenceClaim.summary("sum:" + str(row["accepted_result_id"]),
                                                _digest(str(row.get("accepted_summary") or ""))))
    return _unique(claims)


# --------------------------------------------------------------- 逐项判定
@dataclass(slots=True)
class _Judged:
    refusal: str | None
    refs: list[AssuranceRef]
    deadline: int | None = None


def _pin(store: Any, kind: str, sql: str, args: tuple[Any, ...]) -> AssuranceRef | None:
    row = store.connection.execute(sql, args).fetchone()
    if row is None:
        return None
    identity, revision, body = row
    return AssuranceRef(kind, Pin(str(identity), int(revision), fingerprint(decode(body))))


def _acceptance_ref(store: Any, mission_id: str, acceptance_id: str) -> AssuranceRef | None:
    return _pin(store, "acceptance", "SELECT acceptance_id, 0, acceptance_json FROM acceptances "
                "WHERE mission_id=? AND acceptance_id=?", (mission_id, acceptance_id))


def _observation_ref(store: Any, mission_id: str, observation_id: str) -> AssuranceRef | None:
    return _pin(store, "observation", "SELECT observation_id, 0, observation_json FROM observations "
                "WHERE mission_id=? AND observation_id=?", (mission_id, observation_id))


def _subject_ref(store: Any, mission_id: str, subject: tuple[str, str]) -> AssuranceRef | None:
    """消费方自己的对象：一步的现行任务契约，或任务的现行要求（规划）。"""
    kind, identity = subject
    if kind == "task":
        return _pin(store, "task", "SELECT task_id, binding_revision, binding_json FROM task_semantics "
                    "WHERE mission_id=? AND task_id=? ORDER BY binding_revision DESC LIMIT 1",
                    (mission_id, identity))
    if kind == "requirements" and identity == mission_id:
        return _pin(store, "requirements", "SELECT revision_id, revision, revision_json FROM "
                    "requirements_revisions WHERE mission_id=? ORDER BY revision DESC LIMIT 1", (mission_id,))
    raise AssuranceError("USE_SUBJECT_INVALID", kind)


def _judge_knowledge(store: Any, mission_id: str, claim: EvidenceClaim) -> _Judged:
    from ..memory.knowledge_standing import CURRENT, knowledge_standing

    record = store.get_knowledge(claim.id)
    if record is None or record.mission_id != mission_id:
        return _Judged(f"{claim.label}:missing", [])
    if int(record.version) != claim.version:
        return _Judged(f"{claim.label}:version_changed", [])
    standing = knowledge_standing(store, record)
    if standing != CURRENT:
        return _Judged(f"{claim.label}:{standing}", [])
    ref = _acceptance_ref(store, mission_id, str(record.support["acceptance_id"]))
    return _Judged(None, [] if ref is None else [ref])


def _judge_summary(store: Any, mission_id: str, claim: EvidenceClaim, rows: list[Mapping[str, Any]]) -> _Judged:
    from ..memory.knowledge_standing import acceptance_is_current
    from .assurance_validity import result_acceptance_ids

    row = next((item for item in rows if item["id"] == claim.id), None)
    if row is None:
        return _Judged(f"{claim.label}:not_current", [])
    if row["summary_sha256"] != claim.version:
        return _Judged(f"{claim.label}:changed", [])
    result_id = claim.id.removeprefix("sum:")
    refs = []
    for acceptance in result_acceptance_ids(store, mission_id, str(row["source_task"]), result_id):
        if acceptance_is_current(store, mission_id, acceptance)[0]:
            ref = _acceptance_ref(store, mission_id, acceptance)
            if ref is not None:
                refs.append(ref)
    return _Judged(None, refs)


def _judge_step_ground(store: Any, mission_id: str, task_id: str, now_ms: int) -> _Judged:
    """一步的开工地基（原 ``ActionCommits._validity_refusal``，阶段 C 第 5 条，整段搬来）。

    这一步的有效性见证按"取了哪样东西"各读最新一张。它此刻依据的验收：最新见证须可用、在作用域
    现行纪元下新鲜（见证合同自己的 ``is_fresh_for``）。见证所依据的验收已不当前时，若同一上游已有
    当前验收、这一步也对它持有好见证（上游重做过、重读过），旧的算历史；否则地基没了。
    """
    import json

    from ..contracts.evidence_state import ValidityWitness, WitnessDecision
    from ..contracts.semantic_base import TypedRefKind
    from ..memory.knowledge_standing import acceptance_is_current
    from ..storage.htn_store import HtnStore
    from ..storage.store import StoreError

    htn = HtnStore(store)
    latest: dict[tuple[str, str], ValidityWitness] = {}
    for digest, raw in store.connection.execute(
            "SELECT subject_digest, witness_json FROM validity_witnesses WHERE mission_id=?"
            " AND consumer_kind='task' AND consumer_id=? ORDER BY as_of_ms, witness_id",
            (mission_id, task_id)):
        witness = ValidityWitness.from_json(json.loads(raw))
        latest[(str(digest), str(witness.purpose))] = witness

    def producer_of(acceptance_id: str) -> str | None:
        try:
            return str(htn.get_acceptance(acceptance_id).task_id)
        except StoreError:
            return None

    covered: set[str] = set()  # producers whose current Acceptance this step reads
    history: list[tuple[str, str]] = []  # (acceptance no longer current, why)
    refs: list[AssuranceRef] = []
    deadlines: list[int] = []
    for witness in latest.values():
        supports = [str(ref.id) for ref in witness.support_refs if ref.kind is TypedRefKind.ACCEPTANCE]
        stale = [(item, why) for item in supports
                 for ok, why in [acceptance_is_current(store, mission_id, item)] if not ok]
        if stale:
            history.extend(stale)
            continue
        if witness.decision is not WitnessDecision.USABLE:
            return _Judged(f"witness_{str(witness.decision).lower()}:{witness.witness_id}", [])
        current = htn.epoch(mission_id, witness.scope_id)
        if not witness.is_fresh_for(now_ms=now_ms, current_scope_epoch=current):
            return _Judged(f"scope_epoch:{witness.scope_id}:{witness.scope_epoch}->{current}", [])
        covered.update(filter(None, map(producer_of, supports)))
        if witness.not_after_ms is not None:
            deadlines.append(int(witness.not_after_ms))
        for item in witness.support_refs:
            if item.kind is TypedRefKind.ACCEPTANCE:
                ref = _acceptance_ref(store, mission_id, str(item.id))
            elif item.kind is TypedRefKind.OBSERVATION:
                ref = _observation_ref(store, mission_id, str(item.id))
            else:
                ref = None
            if ref is not None:
                refs.append(ref)
    for acceptance_id, why in history:
        if producer_of(acceptance_id) not in covered:
            return _Judged(f"{why}:{acceptance_id}", [])
    return _Judged(None, refs, min(deadlines) if deadlines else None)


def _judge_all(store: Any, mission_id: str, claims: Sequence[EvidenceClaim],
               now_ms: int) -> tuple[list[str], list[AssuranceRef], list[int]]:
    refusals: list[str] = []
    refs: list[AssuranceRef] = []
    deadlines: list[int] = []
    summaries: list[Mapping[str, Any]] | None = None
    for claim in claims:
        if claim.kind == "knowledge":
            judged = _judge_knowledge(store, mission_id, claim)
        elif claim.kind == "summary":
            if summaries is None:
                from ..context.knowledge_tools import step_summaries

                summaries = step_summaries(store, mission_id)
            judged = _judge_summary(store, mission_id, claim, summaries)
        elif claim.kind == "exact":
            # 精确对象：存在、哈希、归属由下面钉住时的原精确读者核，这里不加判断
            judged = _Judged(None, [claim.ref])
        else:
            judged = _judge_step_ground(store, mission_id, claim.id, now_ms)
        if judged.refusal is not None:
            refusals.append(judged.refusal)
            continue
        refs.extend(judged.refs)
        if judged.deadline is not None:
            deadlines.append(judged.deadline)
    return refusals, refs, deadlines


# ------------------------------------------------------------------ 签发
def certify_point_use_locked(
    commit: Any,
    *,
    mission_id: str,
    purpose: str,
    consumer_kind: str,
    consumer_id: str,
    claims: Sequence[EvidenceClaim],
    subject: tuple[str, str],
    record: bool = True,
    caller: tuple[str, str] | None = None,
) -> PointUse:
    """在消费方自己的事务里签一张时点证书（``record=False``：只核不落库，供复核用）。

    没报证据：这次没用证据，不签也不拦。报了证据：全部当前才签；否则返回具名原因，什么都不写。
    ``caller``（主体编号，租户）：调用者从外面来（原生下载）时给，须就是这个部署的主体与租户。
    """
    store = commit.store
    claims = _unique(tuple(claims))
    use = PointUse(purpose, text(mission_id), text(consumer_kind), text(consumer_id), claims)
    if not store.connection.in_transaction:
        raise AssuranceError("READ_TRANSACTION_REQUIRED")
    if purpose == "MAINTAIN":
        return _refused(use, MAINTAIN_MONITOR_UNAVAILABLE)
    if purpose not in POINT_PURPOSES:
        raise AssuranceError("USE_PURPOSE_UNSUPPORTED", purpose)
    if not claims:
        return use
    validity = getattr(commit, "_assurance_validity", None)
    gate = getattr(commit, "_assurance_root_gate", None)
    if validity is None or gate is None:
        return _refused(use, USE_CERTIFICATE_REQUIRED)
    if caller is not None and tuple(caller) != (validity.principal_id, validity.tenant_id):
        return _refused(use, "ROOT_READ_NOT_AUTHORIZED")
    try:
        root = gate.require_execution()
        if AssuranceStore(store).lane(mission_id) != "ASSURANCE_1_1":
            return _refused(use, "ASSURANCE_PROFILE_REQUIRED")
        now_ms = integer(int(store.now * 1000))
        epochs = read_epochs_locked(store.connection, mission_id)
        if clock_discontinuous(epochs, now_ms=now_ms):
            return _refused(use, "TIME_DISCONTINUITY")
    except AssuranceError as error:
        return _refused(use, error.code)

    # 1. 逐项用原有判定核此刻是否当前
    refusals, refs, deadlines = _judge_all(store, mission_id, claims, now_ms)
    if refusals:
        return _refused(use, *refusals)

    # 2. 钉住精确对象，逐个取现行授权
    own = _subject_ref(store, mission_id, subject)
    if own is None:
        return _refused(use, f"subject:{subject[0]}:{subject[1]}:missing")
    identity = UseIdentity(mission_id, consumer_kind, consumer_id, mission_id,
                           validity.principal_id, purpose, root.root_incarnation_id)
    reader = AssuranceReader(store, tenant_id=validity.tenant_id, mission_id=mission_id,
                             authority=validity.authority, identity=identity)
    reads: list[ReadItem] = []
    pinned = sorted(set([own, *refs]), key=lambda ref: ref.key)
    own_permission = None
    try:
        for ref in pinned:
            resolved = reader.read_exact_metadata(ref, now_ms=now_ms)
            permission = resolved.permission
            reads.extend((resolved.read_item, permission.access, permission.policy))
            deadlines.append(permission.not_after_ms)
            if ref == own:
                own_permission = permission
    except AssuranceError as error:
        return _refused(use, f"{error.code}:{ref.kind}:{ref.pin.id}")
    reads.append(ReadItem(
        "QUERY_SET",
        canonical({"reader_version": READER_VERSION, "mission": mission_id, "scope": mission_id,
                   "query_kind": "point_use:" + purpose,
                   "proposition_keys": [fingerprint([claim.to_json() for claim in claims])],
                   "polarity": "BOTH"}),
        fingerprint({"claims": [claim.to_json() for claim in claims], "standing": "CURRENT",
                     "pins": [ref.to_json() for ref in pinned]}),
    ))
    policy_row = store.connection.execute(
        "SELECT policy_hash FROM assurance_mission_bindings WHERE mission_id=?", (mission_id,)).fetchone()
    if policy_row is None:
        return _refused(use, "ASSURANCE_PROFILE_UNBOUND")
    not_after_ms = min(deadlines)
    if not_after_ms <= now_ms:
        return _refused(use, "CERTIFICATE_EXPIRED_AT_ISSUE")

    # 3. 组证书，过与 ACCEPT 同一个最终核对
    try:
        certificate = UseCertificate(
            mission_id=mission_id, consumer_kind=consumer_kind, consumer_id=consumer_id,
            scope_id=mission_id, principal_id=validity.principal_id, purpose=purpose,
            truth="TRUE", freshness="CURRENT", availability="READABLE", decision="USABLE",
            coverage="COMPLETE", policy_ref=Pin("assurance-exec-v1.1", 1, policy_row["policy_hash"]),
            read_set=_merge_reads(reads), clean_support_refs=tuple(pinned), issued_at_ms=now_ms,
            not_after_ms=not_after_ms, reasons=(f"point_use:{purpose}", f"evidence_count:{len(claims)}"),
            mission_epoch=epochs.mission, environment_epoch=epochs.environment,
            clock_generation=epochs.clock_generation, root_incarnation_id=root.root_incarnation_id,
        )
        final = read_epochs_locked(store.connection, mission_id)
        check_certificate_binding(
            certificate, identity=identity, mission_epoch=final.mission,
            environment_epoch=final.environment, clock_generation=final.clock_generation,
            clock_state=final.clock_state, now_ms=now_ms,
            current_access=own_permission.access, current_policy=own_permission.policy,
        )
    except AssuranceError as error:
        return _refused(use, error.code)
    certificate_id = "assurance-point-use:" + fingerprint({
        "consumer_kind": consumer_kind, "consumer_id": consumer_id, "purpose": purpose,
        "mission_id": mission_id, "issued_at_ms": now_ms,
        "read_set_hash": fingerprint(certificate.to_json()["read_set"]),
    })
    issued = PointUse(purpose, mission_id, consumer_kind, consumer_id, claims, (), certificate_id, certificate)
    if record:
        record_point_use_locked(store, issued)
    return issued


def _refused(use: PointUse, *reasons: str) -> PointUse:
    return PointUse(use.purpose, use.mission_id, use.consumer_kind, use.consumer_id, use.claims,
                    tuple(dict.fromkeys(reasons)))


def record_point_use_locked(store: Any, use: PointUse) -> None:
    """把签出的证书落库：证书表、依赖索引、一条使用回执（回执里记证据声明）。在消费方的事务里调。"""
    certificate = use.certificate
    assert certificate is not None and use.certificate_id is not None
    receipt = {
        "mission_id": certificate.mission_id,
        "certificate_id": use.certificate_id,
        "certificate_hash": fingerprint(certificate.to_json()),
        "consumer_kind": certificate.consumer_kind,
        "consumer_id": certificate.consumer_id,
        "purpose": certificate.purpose,
        "scope_id": certificate.scope_id,
        "evidence": [claim.to_json() for claim in use.claims],
    }
    receipt_id = "assurance-use-certified:" + use.certificate_id
    with atomic(store):
        if not AssuranceStore(store).record_certificate(use.certificate_id, certificate):
            # 同一次使用重签出逐字节相同的证书（同一毫秒、同一读集）：已经记过，不再写
            existing = store.get_receipt(receipt_id)
            if existing is None or dict(existing) != receipt:
                raise AssuranceError("IMMUTABLE_IDENTITY_CONFLICT", "assurance_use_certificates")
            return
        store.insert_receipt(commit_id=receipt_id, kind=USE_CERTIFIED_KIND, subject_id=use.certificate_id,
                             base_version=0, proposal_hash=fingerprint(receipt), receipt=receipt)


def require_point_use_locked(commit: Any, **kwargs: Any) -> PointUse:
    """签发，签不出就抛 :class:`PointUseRefused`（消费方的事务随之回滚）。"""
    use = certify_point_use_locked(commit, **kwargs)
    if not use.usable:
        raise PointUseRefused(use)
    return use


# ---------------------------------------------------------- 复核与恢复读
def issued_claims(store: Any, mission_id: str, *, consumer_kind: str, consumer_id: str,
                  purpose: str) -> tuple[EvidenceClaim, ...] | None:
    """这个消费方最近一张某用途时点证书的证据声明；没签过返回 None。"""
    row = store.connection.execute(
        "SELECT r.receipt_json FROM assurance_use_certificates c JOIN commit_receipts r "
        "ON r.kind=? AND r.subject_id=c.certificate_id WHERE c.mission_id=? AND c.consumer_kind=? "
        "AND c.consumer_id=? AND c.purpose=? ORDER BY c.rowid DESC LIMIT 1",
        (USE_CERTIFIED_KIND, mission_id, consumer_kind, consumer_id, purpose)).fetchone()
    if row is None:
        return None
    return tuple(EvidenceClaim.from_json(item) for item in decode(row[0])["evidence"])


def planning_evidence_stale(commit: Any, mission_id: str, request_id: str) -> str | None:
    """规划回复准入时复核：这个请求签过的 PLAN 证据此刻是否仍当前（只核不落库）。

    仍当前（或这个请求没用证据）返回 None；否则返回写明哪一项、为什么的原因。"""
    store = commit.store
    with store.read_view():
        claims = issued_claims(store, mission_id, consumer_kind=PLANNING_REQUEST_CONSUMER,
                               consumer_id=request_id, purpose="PLAN")
        if not claims:
            return None
        use = certify_point_use_locked(
            commit, mission_id=mission_id, purpose="PLAN", consumer_kind=PLANNING_REQUEST_CONSUMER,
            consumer_id=request_id, claims=claims, subject=("requirements", mission_id), record=False)
    return None if use.usable else "; ".join(use.refusals)


def knowledge_handover(commit: Any, *, mission_id: str, consumer_id: str, task_id: str,
                       purpose: str = "CONTEXT") -> Any:
    """运行中读一条已验证知识 / 核对过的摘要：交出正文前与装上下文同一道门——签一张证书（消费方是这次
    工具调用），核时钟、授权、根实例并留证书（裁决 2026-10-07 第 5 件）。执行者读是 CONTEXT；审阅员
    读黑板是披露 DISCLOSE（推后第 2 批 A03），``task_id`` 是审阅所属的那一步。返回的函数对一项证据
    声明给出拒绝原因（空 = 可以交出）。"""

    def handover(claim: EvidenceClaim) -> tuple[str, ...]:
        with commit.store.transaction():
            use = certify_point_use_locked(
                commit, mission_id=mission_id, purpose=purpose, consumer_kind=TOOL_CALL_CONSUMER,
                consumer_id=consumer_id, claims=(claim,), subject=("task", task_id))
        return use.refusals

    return handover


def certify_recovery_locked(commit: Any, attempt: Any) -> PointUse:
    """重启后恢复一次在途尝试之前，按它开工时装进上下文的证据重签 RECOVERY（§12.2：冻结的请求
    不静默替换上下文）。开工时没用证据：没什么可核。"""
    claims = issued_claims(commit.store, attempt.mission_id, consumer_kind=ATTEMPT_CONSUMER,
                           consumer_id=attempt.id, purpose="CONTEXT") or ()
    return certify_point_use_locked(
        commit, mission_id=attempt.mission_id, purpose="RECOVERY", consumer_kind=ATTEMPT_CONSUMER,
        consumer_id=attempt.id, claims=claims, subject=("task", attempt.task_id))


__all__ = (
    "ACTION_CONSUMER",
    "ATTEMPT_CONSUMER",
    "MAINTAIN_MONITOR_UNAVAILABLE",
    "NATIVE_READ_CONSUMER",
    "REVIEW_INTENT_CONSUMER",
    "PLANNING_REQUEST_CONSUMER",
    "TOOL_CALL_CONSUMER",
    "USE_CERTIFICATE_REQUIRED",
    "EvidenceClaim",
    "PointUse",
    "PointUseRefused",
    "certify_point_use_locked",
    "certify_recovery_locked",
    "context_claims",
    "issued_claims",
    "knowledge_handover",
    "planning_claims",
    "planning_evidence_stale",
    "record_point_use_locked",
    "require_point_use_locked",
)
