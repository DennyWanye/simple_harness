"""Host-owned history dependency proof.

2026-09-10：认知记忆 SDK（``simple-harness-memory-sdk``）整条移除后，这里只剩
**Host 自己的证据依赖证明**：读回 S1 证据对、逐条校验 envelope/receipt 承诺、
遍历依赖 DAG（环/深度/边数/哈希不匹配一律判不可见）。

原先决定「某条证据是否对本次读可见」的那一次公开 Memory 批（
``HistoryVisibilitySnapshot``）已经没有了。没有记忆系统就没有遗忘/抑制权威，
所以**遍历通过即可见**——这是诚实的语义，不是放行：链路损坏、哈希对不上、
终态未被 Host 身份表证实的根，依然返回不可见。
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from simple_harness import (
    DisclosureContext,
    SanitizedEvidenceEnvelope,
    SanitizedEvidenceReceipt,
)

from deskpet.task_scope.protocol import (
    canonical_hash,
    canonical_json,
    digest,
    identifier,
    redact_credential_shapes,
)

MAX_BINDINGS = 256
MAX_DEPTH = 64
MAX_EDGES = 4096
# 2026-09-08 HM-TO-A6：包住的真实异常必须能落到 Host 日志/审计里。只带
# 「类型 + 稳定消息」，不带任何 envelope/payload 字节，并再过一遍凭据形状红线。
MAX_CAUSE_DETAIL = 200
# 一条 S1 证据被 Memory 结构性拒绝（永久、逐 envelope、与策略无关）的稳定码。
SOURCE_UNADMISSIBLE = "primary_visibility_source_unadmissible"


@lru_cache(maxsize=1)
def _stable_message_types() -> tuple[type, ...]:
    """Error types whose ``str(exc)`` is a stable code, never free-form text.

    Task 6 review F-3: an arbitrary innermost cause can carry a raw HTTP body,
    a SQLite statement or a provider URL.  Only a ``.code`` attribute or one of
    these types is proof that the message is a bounded, content-free token.

    2026-09-10：记忆 SDK 的稳定错误类（``MemoryLimitError`` 等）随其一并移除，
    白名单只剩 Host 自己的稳定错误类型。
    """

    from deskpet.task_scope.protocol import TaskScopeProtocolError

    return (TaskScopeProtocolError,)


def cause_fields(exc: BaseException | None) -> dict[str, str | None]:
    """Payload-free (type, stable code) of a wrapped cause, for Host logs only.

    The type name is always safe to record.  The *message* is recorded only
    when it is a stable code by construction — the exception exposes ``.code``,
    or its class is one of the allowlisted Host/SDK stable-error types.  Any
    other cause contributes its type and nothing else, so an audit record can
    never carry a raw HTTP body, SQL text or a provider URL.
    """

    if exc is None:
        return {"cause_type": None, "cause_detail": None}
    fields = {"cause_type": type(exc).__name__, "cause_detail": None}
    code = getattr(exc, "code", None)
    if isinstance(exc, _stable_message_types()):
        # The message wins over ``.code`` for these: the class carries a
        # *generic* class-level code while its message is the specific one we
        # actually need.
        message = str(exc)
    elif isinstance(code, str) and code:
        message = code
    else:
        return fields
    # Belt and braces: a "stable" code is still redacted and bounded before it
    # reaches a durable audit row.
    detail, _ = redact_credential_shapes(message)
    fields["cause_detail"] = detail[:MAX_CAUSE_DETAIL] or None
    return fields


class PrimaryVisibilityError(ValueError):
    def __init__(self, code: str, cause: BaseException | None = None):
        self.code = code
        fields = cause_fields(cause)
        # 只记类型与稳定消息：`primary_read_policy_unavailable` 这类稳定码此前
        # 把真实原因（例如 SDK 的 MemoryLimitError）整个吞掉，线上只剩一个无从
        # 下手的码（实测 2026-09-08 native-a6-7cec5249）。
        self.cause_type = fields["cause_type"]
        self.cause_detail = fields["cause_detail"]
        super().__init__(code)


async def read_evidence_pair(*, db, subject, primary_ref, evidence_id):
    """Read and verify existing Host S1 bytes; never synthesize an admission."""
    cursor = await db.execute(
        "SELECT e.*,s.receipt_json,s.receipt_sha256 FROM human_memory_evidence e "
        "JOIN human_memory_sanitization_receipts s ON s.receipt_id=e.receipt_id "
        "AND s.evidence_id=e.evidence_id AND s.subject=e.subject "
        "AND s.run_id=e.run_id AND s.envelope_sha256=e.envelope_sha256 "
        "AND s.source_sha256=e.source_sha256 AND s.sanitized_sha256=e.sanitized_sha256 "
        "WHERE e.evidence_id=? AND e.subject=? AND e.primary_conversation_id=? LIMIT 2",
        (evidence_id, subject, primary_ref),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    if len(rows) != 1:
        raise PrimaryVisibilityError("primary_source_missing")
    row = rows[0]
    try:
        envelope = SanitizedEvidenceEnvelope.from_json(json.loads(row["envelope_json"]))
        receipt = SanitizedEvidenceReceipt.from_json(json.loads(row["receipt_json"]))
        receipt.verify(envelope)
        if not receipt.accepted or any(
            (
                envelope.subject != subject,
                envelope.evidence_id != evidence_id,
                envelope.run_id != row["run_id"],
                envelope.source_kind.value != row["source_kind"],
                envelope.source_ref != row["source_ref"],
                envelope.source_hash != row["source_sha256"],
                envelope.sanitized_hash != row["sanitized_sha256"],
                envelope.envelope_hash != row["envelope_sha256"],
                receipt.receipt_id != row["receipt_id"],
                receipt.receipt_hash != row["receipt_sha256"],
                canonical_hash(envelope.to_json()["sanitized_payload"])
                != envelope.sanitized_hash,
                canonical_json(envelope.to_json()["sanitized_payload"])
                != canonical_json(json.loads(row["payload_json"])),
            )
        ):
            raise ValueError("Host S1 row mismatch")
        return envelope, receipt
    except (ValueError, TypeError, KeyError) as exc:
        raise PrimaryVisibilityError("primary_source_corrupt", exc) from exc


def _fields(value, keys):
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise PrimaryVisibilityError("primary_visibility_dependencies_invalid")


def _dependencies(proof):
    """Validate a recorded visibility-dependency proof and key its lanes.

    2026-09-10：``recall`` / ``short_horizon`` / ``procedure_drafts`` 三条泳道
    原本要构造记忆 SDK 的 binding 对象再交给公开可见性批。SDK 移除后这里只保留
    **形状校验**，并把每条泳道项折算成一个不可伪造的规范哈希键；键只用于本模块
    内部的「这条依赖是否被证明」记账，不再代表任何记忆权威。
    """

    if not isinstance(proof, Mapping):
        raise PrimaryVisibilityError("primary_visibility_dependencies_invalid")
    version = proof.get("schema_version")
    if type(version) is not int or version not in (1, 2, 3):
        raise PrimaryVisibilityError("primary_visibility_dependencies_invalid")
    lanes = ("evidence", "recall") if version == 1 else ("evidence", "recall", "short_horizon")
    if version == 3: lanes += ("procedure_drafts",)
    _fields(proof, ("schema_version", *lanes))
    if not all(isinstance(proof[k], (list, tuple)) for k in lanes):
        raise PrimaryVisibilityError("primary_visibility_dependencies_invalid")
    if sum(len(proof[k]) for k in lanes) > MAX_BINDINGS:
        raise PrimaryVisibilityError("primary_visibility_limit")
    evidence, recalls = {}, []
    for item in proof["evidence"]:
        _fields(item, ("evidence_id", "envelope_hash"))
        identifier(item["evidence_id"], "evidence_id", 512)
        digest(item["envelope_hash"], "envelope_hash")
        if (
            item["evidence_id"] in evidence
            and evidence[item["evidence_id"]] != item["envelope_hash"]
        ):
            raise PrimaryVisibilityError("primary_visibility_binding_mismatch")
        evidence[item["evidence_id"]] = item["envelope_hash"]
    for item in proof["recall"]:
        _fields(item, ("result_id", "result_hash", "item_id", "item_hash"))
        recalls.append(_lane_key("recall", item))
    if version >= 2:
        for item in proof["short_horizon"]:
            _fields(item, ("audit_id", "chunk_ref", "content_hash"))
            recalls.append(_lane_key("short_horizon", item))
    if version == 3:
        for item in proof["procedure_drafts"]:
            _fields(item, ("memory_id","revision","candidate_hash"))
            recalls.append(_lane_key("procedure_drafts", item))
    return evidence, tuple(recalls)


def _lane_key(lane: str, item) -> str:
    """Content-addressed key for one recorded non-evidence dependency item."""
    return canonical_hash(
        {"domain": "host.primary.visibility.lane.v1", "lane": lane, "item": dict(item)}
    )


def _evidence_key(evidence_id: str, envelope_hash: str) -> str:
    """Key for one proven S1 evidence node inside a single dependency proof."""
    return canonical_hash(
        {
            "domain": "host.primary.visibility.evidence.v1",
            "evidence_id": evidence_id,
            "envelope_hash": envelope_hash,
        }
    )


def inline_evidence_limit() -> int | None:
    """No Memory system ⇒ no inline-payload ceiling imposed by a Memory batch.

    ``None`` is the existing "no ceiling known" answer every caller already
    handles by writing the payload unchanged.
    """
    return None


def assert_source_admissible(envelope, receipt) -> None:
    """No-op in a build with no Memory system.

    2026-09-08 HM-TO-A6 装了这道门，是因为一条超过记忆 SDK 64 KiB 内联上限的
    终态观测会让**之后每一批**历史可见性调用抛 ``MemoryLimitError``，整条主对话
    读不出来。那个批已经不存在了：没有记忆系统，就没有会被它结构性拒绝的证据。

    保留函数与调用点（而不是删掉），是因为「哪些来源可被下游权威接纳」这件事在
    编排层大改后大概率要回来；此时它诚实地什么都不判，不假装通过某个不存在的
    校验。
    """

    return None


class PrimaryHistoryPolicy:
    """Host dependency-proof policy for one subject's primary conversation.

    The third constructor argument used to be the public Memory
    history-visibility checker.  It is gone with the Memory SDK; the parameter
    is retired rather than silently ignored.
    """

    def __init__(self, db_path: str | Path, subject: str):
        self.path, self.subject = Path(db_path), subject

    async def check_evidence_ids(
        self,
        *,
        db,
        primary_ref: str,
        evidence_ids: tuple[str, ...],
        disclosure_context: DisclosureContext,
    ) -> dict[str, bool]:
        visible, _ = await self._check(
            db=db,
            primary_ref=primary_ref,
            evidence_ids=evidence_ids,
            disclosure_context=disclosure_context,
        )
        return visible

    async def check_dependencies(
        self,
        *,
        db,
        primary_ref: str,
        dependencies: Mapping[str, object],
        disclosure_context: DisclosureContext,
    ) -> bool:
        """Check in-flight Run dependencies directly, without fake terminal evidence."""
        try:
            evidence, recall = _dependencies(dependencies)
            # Runtime must retain at least its real current USER source.
            if not evidence:
                return False
            visible, recall_visible = await self._check(
                db=db,
                primary_ref=primary_ref,
                evidence_ids=tuple(evidence),
                disclosure_context=disclosure_context,
                expected_hashes=evidence,
                recall=recall,
            )
            return all(visible.values()) and recall_visible
        except (PrimaryVisibilityError, ValueError, TypeError, KeyError):
            return False

    async def current_user_denial(self, *, db, primary_ref, evidence_id, evidence_hash, disclosure_context):
        """Always ``None``: no Memory system ⇒ no authority that can deny a source.

        原来这条路径唯一的判据是那次公开 Memory 可见性批的逐条 ``visible``
        判定（用户在记忆面板里「忘掉」了这条当前 USER 来源）。记忆系统于
        2026-09-10 整条移除后，没有任何权威能给出「被证明的拒绝」，所以这里
        诚实地回答"没有证明"，而不是拿遍历失败去伪造一次拒绝——遍历失败是
        「读不出来」，与「被拒绝披露」是两回事。

        结果：``PreparationDisclosureRejected`` 在本构建下不会再产生新记录。
        """

        return None

    async def _check(
        self,
        *,
        db,
        primary_ref,
        evidence_ids,
        disclosure_context,
        expected_hashes=None,
        recall=(),
    ):
        """Prove each root's dependency DAG; no Memory snapshot decides here.

        2026-09-10：唯一那次公开 Memory 可见性批已随记忆 SDK 移除。本方法保留
        全部 Host 侧结构证明——S1 证据对读回与承诺校验、envelope 哈希必须与
        依赖声明一致、环/深度/边数上限、终态观测必须被 Host 终态身份表证实、
        终态的当前 USER 输入必须落在自己的依赖闭包里——遍历不过的根返回不可见。
        """

        identifier(primary_ref, "primary_ref", 512)
        if (
            type(disclosure_context) is not DisclosureContext
            or disclosure_context.subject != self.subject
        ):
            raise PrimaryVisibilityError("primary_visibility_context_invalid")
        if (
            not isinstance(evidence_ids, (list, tuple))
            or len(evidence_ids) > MAX_BINDINGS
        ):
            raise PrimaryVisibilityError("primary_visibility_limit")
        for evidence_id in evidence_ids:
            identifier(evidence_id, "evidence_id", 512)
        proven, memo, active = set(), {}, set()
        edges = 0

        def add(key):
            proven.add(key)
            if len(proven) > MAX_BINDINGS:
                raise PrimaryVisibilityError("primary_visibility_limit")
            return key

        async def visit(evidence_id, expected_hash=None, depth=0):
            nonlocal edges
            edges += 1
            if depth >= MAX_DEPTH or edges > MAX_EDGES:
                raise PrimaryVisibilityError("primary_visibility_limit")
            if evidence_id in active:
                raise PrimaryVisibilityError("primary_visibility_cycle")
            if evidence_id in memo:
                actual_hash, dependency_keys = memo[evidence_id]
                if expected_hash is not None and expected_hash != actual_hash:
                    raise PrimaryVisibilityError("primary_visibility_binding_mismatch")
                return dependency_keys
            envelope, receipt = await read_evidence_pair(
                db=db,
                subject=self.subject,
                primary_ref=primary_ref,
                evidence_id=evidence_id,
            )
            if expected_hash is not None and expected_hash != envelope.envelope_hash:
                raise PrimaryVisibilityError("primary_visibility_binding_mismatch")
            # Retired gate, kept as the seam a later admission authority reuses.
            assert_source_admissible(envelope, receipt)
            active.add(evidence_id)
            try:
                required = {add(_evidence_key(evidence_id, envelope.envelope_hash))}
                evidence_dependencies = [
                    (ref.evidence_id, ref.content_hash)
                    for ref in envelope.evidence_refs
                ]
                payload = envelope.sanitized_payload
                terminal = envelope.source_kind.value == "runtime_event" and (
                    envelope.source_ref.startswith("primary-runtime:")
                    or payload.get("kind") == "primary_run_terminal"
                )
                input_id = None
                if terminal:
                    evidence_proof, recall_proof = _dependencies(
                        payload.get("visibility_dependencies")
                    )
                    # 记忆 SDK 移除后不再有 prospective 来源清单可对账，
                    # ``prospective_source_dependencies`` 字段（若旧记录里还有）
                    # 只是不再被检查的历史字节，不参与任何判定。
                    from deskpet.execution.terminal_identity import (
                        read_primary_terminal_identity_tx,
                    )

                    identity = await read_primary_terminal_identity_tx(
                        db,
                        subject=self.subject,
                        primary_ref=primary_ref,
                        host_run_id=payload.get("host_run_id"),
                        sdk_run_id=envelope.run_id,
                    )
                    if (
                        identity is None
                        or identity.observation_evidence_id != evidence_id
                    ):
                        raise PrimaryVisibilityError(
                            "primary_visibility_terminal_unverified"
                        )
                    cursor = await db.execute(
                        "SELECT t.evidence_id,t.evidence_hash FROM foreground_turns t "
                        "JOIN foreground_runs r ON r.turn_id=t.turn_id AND r.subject=t.subject "
                        "WHERE r.host_run_id=? AND t.turn_id=? AND t.subject=? "
                        "AND t.primary_conversation_id=? AND r.primary_conversation_id=? LIMIT 1",
                        (
                            payload.get("host_run_id"),
                            payload.get("turn_id"),
                            self.subject,
                            primary_ref,
                            primary_ref,
                        ),
                    )
                    input_row = await cursor.fetchone()
                    await cursor.close()
                    if input_row is None:
                        raise PrimaryVisibilityError(
                            "primary_visibility_terminal_unverified"
                        )
                    input_id = input_row[0]
                    evidence_dependencies.extend(evidence_proof.items())
                    for lane_key in recall_proof:
                        required.add(add(lane_key))
                for dependency_id, dependency_hash in evidence_dependencies:
                    required.update(
                        await visit(dependency_id, dependency_hash, depth + 1)
                    )
                if input_id is not None:
                    input_binding = memo.get(input_id)
                    if (
                        input_binding is None
                        or input_binding[0] != input_row[1]
                        or not input_binding[1] <= required
                    ):
                        raise PrimaryVisibilityError(
                            "primary_visibility_input_unproved"
                        )
                memo[evidence_id] = (envelope.envelope_hash, required)
                return required
            finally:
                active.remove(evidence_id)

        extra_keys = {add(lane_key) for lane_key in recall}
        roots = {}
        for evidence_id in dict.fromkeys(evidence_ids):
            try:
                roots[evidence_id] = await visit(
                    evidence_id,
                    None if expected_hashes is None else expected_hashes[evidence_id],
                )
            except PrimaryVisibilityError as exc:
                if exc.code == "primary_visibility_limit":
                    raise
                roots[evidence_id] = None
            except (ValueError, TypeError, KeyError, RuntimeError):
                roots[evidence_id] = None
        # No Memory system ⇒ no suppression authority ⇒ every dependency this
        # traversal actually *proved* is visible.  A root that failed to prove
        # its chain stays invisible, and a recorded non-evidence lane item
        # (legacy recall / short-horizon / procedure-draft proof) counts as
        # proved only because its shape and content hash were validated above.
        return (
            {eid: keys is not None for eid, keys in roots.items()},
            extra_keys <= proven,
        )
