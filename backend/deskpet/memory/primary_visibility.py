"""Host-owned history dependency proof, checked by one public Memory batch."""

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
    """

    allowed: list[type] = []
    try:
        from simple_harness_memory.core import errors as sdk_errors
    except ImportError:  # pragma: no cover - no SDK ⇒ nothing to allowlist
        pass
    else:
        allowed += [
            sdk_errors.MemoryErrorBase,
            sdk_errors.MemoryLimitError,
            sdk_errors.MemoryCorruptionError,
            sdk_errors.EmbeddingError,
        ]
    from deskpet.task_scope.protocol import TaskScopeProtocolError

    allowed.append(TaskScopeProtocolError)
    return tuple(allowed)


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
        # The message wins over ``.code`` for these: an SDK stable-error class
        # carries a *generic* class-level code ("memory_validation_error")
        # while its message is the specific one we actually need
        # ("evidence_credential_boundary_rejected").
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
    try:
        from simple_harness_memory import HistoryRecallBinding
    except ImportError as exc:
        raise PrimaryVisibilityError("primary_read_policy_unavailable", exc) from exc

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
        recalls.append(HistoryRecallBinding(**dict(item)))
    if version >= 2:
        try:
            from simple_harness_memory import HistoryShortHorizonBinding
        except ImportError as exc:
            raise PrimaryVisibilityError("primary_read_policy_unavailable", exc) from exc
        for item in proof["short_horizon"]:
            _fields(item, ("audit_id", "chunk_ref", "content_hash"))
            recalls.append(HistoryShortHorizonBinding(**dict(item)))
    if version == 3:
        from simple_harness_memory import HistoryProcedureDraftBinding
        for item in proof["procedure_drafts"]:
            _fields(item, ("memory_id","revision","candidate_hash"))
            recalls.append(HistoryProcedureDraftBinding(**dict(item)))
    return evidence, recalls


def _binding_hash(binding):
    # Public Memory history v1 binding commitment; no SDK private helper/SQL.
    return canonical_hash(
        {"domain": "memory.history.binding.v1", "payload": binding.to_json()}
    )


def inline_evidence_limit() -> int | None:
    """The Memory inline-payload ceiling this build's SDK will admit."""
    try:
        from simple_harness_memory.core.evidence import MAX_INLINE_EVIDENCE_BYTES
    except ImportError:  # pragma: no cover - no SDK ⇒ every read already fails
        return None
    return int(MAX_INLINE_EVIDENCE_BYTES)


def assert_source_admissible(envelope, receipt) -> None:
    """Reject an S1 pair the Memory batch can never admit (fail-closed).

    2026-09-08 HM-TO-A6: a single terminal observation whose inline payload
    exceeded Memory's 64 KiB ceiling made **every** later history batch raise
    ``MemoryLimitError``.  The Host wrapped that as the transient-looking
    ``primary_read_policy_unavailable`` and the whole primary conversation
    became unreadable — the foreground driver died and no later turn could
    start.

    Structural rejection is a permanent, per-envelope property, not a policy
    outage, so it withholds that one source instead of the batch.  Task 6
    review F-1: this calls **the SDK's own** :func:`validate_sanitized_evidence`
    rather than reimplementing one of its rules, because the batch also dies on
    a malformed ``blob_ref``, a >4096-node or depth->32 structure, an oversized
    public string and a credential-boundary hit — a hand-rolled 64 KiB check
    admitted every one of those and reproduced the stall.

    This only ever withholds a source; no binding becomes visible because of it.
    """

    try:
        from simple_harness_memory.core.errors import (
            MemoryLimitError,
            MemoryValidationError,
        )
        from simple_harness_memory.core.evidence import validate_sanitized_evidence
    except ImportError as exc:  # pragma: no cover - no SDK ⇒ every read fails
        raise PrimaryVisibilityError("primary_read_policy_unavailable", exc) from exc
    from deskpet.memory.human_memory_v7 import HOST_SUPPORTED_FILTER_POLICIES

    try:
        validate_sanitized_evidence(
            envelope,
            receipt,
            supported_filter_policies=tuple(sorted(HOST_SUPPORTED_FILTER_POLICIES)),
        )
    except (MemoryValidationError, MemoryLimitError) as exc:
        raise PrimaryVisibilityError(SOURCE_UNADMISSIBLE, exc) from exc


class PrimaryHistoryPolicy:
    def __init__(self, db_path: str | Path, subject: str, checker: object | None):
        self.path, self.subject, self.checker = Path(db_path), subject, checker

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
        """Return only a validated current-source denial, never a generic false."""
        from simple_harness_memory import HistoryEvidenceBinding
        from deskpet.execution.preparation_rejection import REASONS

        capture = {}
        # Task 6 review F-7: an unadmissible *current* USER source is not
        # "no proven denial" — it is a source this Run can never read back.
        # Let the stable code out instead of degrading it into ``None``.
        visible, _ = await self._check(
            db=db, primary_ref=primary_ref, evidence_ids=(evidence_id,),
            disclosure_context=disclosure_context, expected_hashes={evidence_id: evidence_hash},
            snapshot_capture=capture, propagate_unadmissible=True,
        )
        if visible.get(evidence_id) is not False or "snapshot" not in capture:
            return None
        snapshot = capture["snapshot"]
        for binding in capture["bindings"]:
            if type(binding) is HistoryEvidenceBinding and binding.envelope.evidence_id == evidence_id:
                if binding.envelope.source_kind.value != "user_message":
                    return None
                key = _binding_hash(binding)
                item = next((i for i in snapshot.items if i.binding_hash == key), None)
                if item is not None and not item.visible and item.reason in REASONS:
                    return key, snapshot, item.reason
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
        snapshot_capture=None,
        propagate_unadmissible=False,
    ):
        """All dependency decisions belong to this call's single SDK snapshot."""
        try:
            from simple_harness_memory import (
                HistoryEvidenceBinding,
                HistoryVisibilitySnapshot,
            )
        except ImportError as exc:
            raise PrimaryVisibilityError("primary_read_policy_unavailable", exc) from exc

        identifier(primary_ref, "primary_ref", 512)
        if (
            type(disclosure_context) is not DisclosureContext
            or disclosure_context.subject != self.subject
        ):
            raise PrimaryVisibilityError("primary_visibility_context_invalid")
        if self.checker is None:
            raise PrimaryVisibilityError("primary_read_policy_unavailable")
        if (
            not isinstance(evidence_ids, (list, tuple))
            or len(evidence_ids) > MAX_BINDINGS
        ):
            raise PrimaryVisibilityError("primary_visibility_limit")
        for evidence_id in evidence_ids:
            identifier(evidence_id, "evidence_id", 512)
        bindings, memo, active = {}, {}, set()
        edges = 0

        def add(binding):
            key = _binding_hash(binding)
            bindings[key] = binding
            if len(bindings) > MAX_BINDINGS:
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
            # Checked before the batch is assembled: one unadmissible source
            # must withhold only its own root, never the whole SDK batch.
            assert_source_admissible(envelope, receipt)
            active.add(evidence_id)
            try:
                required = {add(HistoryEvidenceBinding(envelope, receipt))}
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
                    from deskpet.memory.prospective_source_dependencies import verify_terminal_sources_tx
                    await verify_terminal_sources_tx(db, sdk_run_id=envelope.run_id,
                        manifest=payload.get('prospective_source_dependencies'), evidence=evidence_proof)
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
                    for binding in recall_proof:
                        required.add(add(binding))
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

        extra_keys = {add(binding) for binding in recall}
        roots = {}
        for evidence_id in dict.fromkeys(evidence_ids):
            try:
                roots[evidence_id] = await visit(
                    evidence_id,
                    None if expected_hashes is None else expected_hashes[evidence_id],
                )
            except PrimaryVisibilityError as exc:
                if exc.code == "primary_visibility_limit" or (
                    propagate_unadmissible and exc.code == SOURCE_UNADMISSIBLE
                ):
                    raise
                roots[evidence_id] = None
            except (ValueError, TypeError, KeyError, RuntimeError):
                roots[evidence_id] = None
        needed = extra_keys.union(
            *(keys for keys in roots.values() if keys is not None)
        )
        if not needed:
            return {evidence_id: False for evidence_id in roots}, not extra_keys
        ordered = tuple(bindings[key] for key in bindings if key in needed)
        expected = tuple(_binding_hash(binding) for binding in ordered)
        try:
            snapshot = self.checker(
                subject=self.subject,
                disclosure_context=disclosure_context,
                bindings=ordered,
            )
            if inspect.isawaitable(snapshot):
                snapshot = await snapshot
            if (
                type(snapshot) is not HistoryVisibilitySnapshot
                or type(snapshot.schema_version) is not int
                or snapshot.schema_version != 1
                or snapshot.subject != self.subject
                or tuple(item.binding_hash for item in snapshot.items) != expected
                or any(type(item.visible) is not bool for item in snapshot.items)
            ):
                raise ValueError("history visibility response mismatch")
        except Exception as exc:
            # 这里是唯一的 SDK 快照调用点；把真实异常的类型与稳定消息带出去，
            # 否则 `foreground.runtime.failed` 只剩一个空壳码。
            raise PrimaryVisibilityError("primary_read_policy_unavailable", exc) from exc
        if snapshot_capture is not None:
            snapshot_capture.update(snapshot=snapshot, bindings=ordered)
        decisions = {item.binding_hash: item.visible for item in snapshot.items}
        return (
            {
                eid: keys is not None and all(decisions[key] for key in keys)
                for eid, keys in roots.items()
            },
            all(decisions[key] for key in extra_keys),
        )
