"""Host-owned history dependency proof, checked by one public Memory batch."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
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
)

MAX_BINDINGS = 256
MAX_DEPTH = 64
MAX_EDGES = 4096


class PrimaryVisibilityError(ValueError):
    def __init__(self, code: str):
        self.code = code
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
        raise PrimaryVisibilityError("primary_source_corrupt") from exc


def _fields(value, keys):
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise PrimaryVisibilityError("primary_visibility_dependencies_invalid")


def _dependencies(proof):
    try:
        from simple_harness_memory import HistoryRecallBinding
    except ImportError as exc:
        raise PrimaryVisibilityError("primary_read_policy_unavailable") from exc

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
            raise PrimaryVisibilityError("primary_read_policy_unavailable") from exc
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
        visible, _ = await self._check(
            db=db, primary_ref=primary_ref, evidence_ids=(evidence_id,),
            disclosure_context=disclosure_context, expected_hashes={evidence_id: evidence_hash},
            snapshot_capture=capture,
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
    ):
        """All dependency decisions belong to this call's single SDK snapshot."""
        try:
            from simple_harness_memory import (
                HistoryEvidenceBinding,
                HistoryVisibilitySnapshot,
            )
        except ImportError as exc:
            raise PrimaryVisibilityError("primary_read_policy_unavailable") from exc

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
                if exc.code == "primary_visibility_limit":
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
            raise PrimaryVisibilityError("primary_read_policy_unavailable") from exc
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
