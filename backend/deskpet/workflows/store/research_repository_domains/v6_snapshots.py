"""Deep-research v6 snapshot validation, persistence and audit."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import aiosqlite

from ...definitions.deep_research_v6_contracts import ResearchSpecV1, RouteDecisionV1
from ...definitions.deep_research_v6_delivery import TerminalDeliveryManifestV1
from ...definitions.deep_research_v6_evidence import (
    GENESIS_EVIDENCE_HEAD,
    AdmittedResearchFactV1,
    AnswerAssessmentV1,
    EvidenceFactBatchV1,
    RegisteredInferenceV1,
    derive_assessment_input_hash,
)
from ...definitions.deep_research_v6_evidence_contracts import (
    CandidateProducerOutcomeV1,
    EvidenceCandidateBundleV1,
    EvidenceRepairRequestV1,
    InferenceProposalBundleV1,
    ResearchLLMEffectOutcomeV1,
)
from ...definitions.deep_research_v6_integrity import ClaimBatchV1
from ...definitions.deep_research_v6_retrieval_contracts import (
    OfficialSearchResultV1,
    PageAttemptOutcomeV1,
    PageExtractionResultV1,
    SourceLocatorV1,
)
from .core import ResearchRepositoryCore
from .support import (
    _CONTINUABLE_DELIVERY,
    ResearchRepositoryError,
    _canonical,
    _sha256_text,
    _wire_digest,
)

_V6_SNAPSHOT_KEYS = {
    "schema_version",
    "snapshot_id",
    "snapshot_hash",
    "run_id",
    "workflow_name",
    "workflow_version",
    "spec_ref",
    "spec_hash",
    "fact_batch_refs",
    "evidence_head_hash",
    "assessment_ref",
    "assessment_hash",
    "assessment_input_hash",
    "claim_batch_ref",
    "provenance_refs",
    "policy_refs",
    "closure_refs",
}
_V6_POLICY_KEYS = {
    "compiler",
    "route",
    "extraction",
    "llm_extract",
    "llm_repair",
    "llm_inference",
    "admission",
    "inference",
    "assessment",
    "claim",
    "quality",
}
_V6_ROUTE_POLICY_V1_KEYS = {
    "policy_id",
    "parent_deadline_ms",
    "page_deadline_ms",
    "max_queries",
    "max_fetches",
    "max_fetch_concurrency",
    "max_lanes",
    "official_source_policy_hash",
}


def _walk_v6_wire_refs(value: object) -> set[str]:
    """Return forward registered-blob edges from one canonical v6 object.

    Rejected inference proposals deliberately retain unresolved strings under
    ``proposed_premise_fact_refs`` for audit only; they are not closure edges.
    This mirrors the terminal snapshot builder's one permitted exception.
    """

    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "proposed_premise_fact_refs":
                continue
            if key.endswith("_ref") and isinstance(item, str) and item.startswith("sha256:"):
                refs.add(item)
            elif key.endswith("_refs") and isinstance(item, list):
                refs.update(
                    str(ref) for ref in item if isinstance(ref, str) and ref.startswith("sha256:")
                )
            else:
                refs.update(_walk_v6_wire_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_walk_v6_wire_refs(item))
    return refs


class V6SnapshotMixin(ResearchRepositoryCore):
    """Deep-research v6 snapshot validation, persistence and audit."""

    __slots__ = ()

    async def _read_registered_bytes_v6(
        self, db: aiosqlite.Connection, wire_ref: object, *, field: str
    ) -> tuple[str, bytes]:
        if self.blob_root is None:
            raise ResearchRepositoryError(
                "v6_blob_root_missing", "v6 continuation repository requires a registered blob root"
            )
        digest = _wire_digest(wire_ref, field)
        row = await (
            await db.execute(
                "SELECT size_bytes,relative_path FROM workflow_blobs WHERE sha256=?", (digest,)
            )
        ).fetchone()
        if row is None:
            raise ResearchRepositoryError("v6_blob_missing", f"{field} is not registered")
        root = self.blob_root.resolve()
        target = (root / str(row["relative_path"])).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ResearchRepositoryError(
                "v6_blob_path_invalid", f"{field} escaped blob root"
            ) from exc
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise ResearchRepositoryError(
                "v6_blob_missing", f"{field} file is unavailable"
            ) from exc
        if len(data) != int(row["size_bytes"]) or hashlib.sha256(data).hexdigest() != digest:
            raise ResearchRepositoryError(
                "v6_blob_digest_mismatch", f"{field} failed digest validation"
            )
        return digest, data

    async def _read_registered_json_v6(
        self, db: aiosqlite.Connection, wire_ref: object, *, field: str
    ) -> tuple[str, dict[str, Any]]:
        """Decode one registered canonical JSON blob through repository-owned storage."""

        digest, data = await self._read_registered_bytes_v6(db, wire_ref, field=field)
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResearchRepositoryError("v6_blob_json_invalid", f"{field} is not JSON") from exc
        if not isinstance(value, dict) or _canonical(value).encode("utf-8") != data:
            raise ResearchRepositoryError(
                "v6_blob_json_noncanonical", f"{field} is not canonical JSON"
            )
        return digest, value

    @staticmethod
    def _validate_v6_snapshot(value: Mapping[str, Any], *, parent_run_id: str) -> None:
        """Synchronous wire-shape validator retained for retention compatibility."""

        if set(value) != _V6_SNAPSHOT_KEYS:
            raise ResearchRepositoryError(
                "v6_snapshot_keys_invalid", "v6 snapshot exact keys differ"
            )
        if (
            value.get("schema_version") != 1
            or value.get("run_id") != parent_run_id
            or value.get("workflow_name") != "deep_research"
            or value.get("workflow_version") != "v6"
        ):
            raise ResearchRepositoryError(
                "v6_snapshot_identity_invalid", "v6 snapshot identity differs"
            )
        semantic = dict(value)
        snapshot_id = semantic.pop("snapshot_id", None)
        snapshot_hash = semantic.pop("snapshot_hash", None)
        expected = _sha256_text(_canonical(semantic))
        if snapshot_hash != expected or snapshot_id != "rcs_" + expected[:24]:
            raise ResearchRepositoryError(
                "v6_snapshot_hash_invalid", "v6 snapshot semantic hash differs"
            )
        policies = value.get("policy_refs")
        if not isinstance(policies, dict) or set(policies) != _V6_POLICY_KEYS:
            raise ResearchRepositoryError(
                "v6_snapshot_policy_invalid", "v6 snapshot policy refs differ"
            )
        lists = ("fact_batch_refs", "provenance_refs", "closure_refs")
        if any(not isinstance(value.get(name), list) for name in lists):
            raise ResearchRepositoryError(
                "v6_snapshot_refs_invalid", "v6 snapshot refs are not arrays"
            )
        expected_closure = {
            str(value["spec_ref"]),
            str(value["assessment_ref"]),
            str(value["claim_batch_ref"]),
            *[str(item) for item in value["fact_batch_refs"]],
            *[str(item) for item in value["provenance_refs"]],
            *[str(item) for item in policies.values()],
        }
        closure = [str(item) for item in value["closure_refs"]]
        if closure != sorted(set(closure)) or set(closure) != expected_closure:
            raise ResearchRepositoryError(
                "v6_snapshot_closure_invalid", "v6 snapshot closure is not exact"
            )
        for field in ("spec_ref", "assessment_ref", "claim_batch_ref"):
            _wire_digest(value[field], field)
        for ref in closure:
            _wire_digest(ref, "closure_ref")

    async def _validate_v6_snapshot_graph(
        self,
        db: aiosqlite.Connection,
        value: Mapping[str, Any],
        *,
        parent_run_id: str,
    ) -> None:
        """Decode and recompute the exact registered v6 continuation graph."""

        if set(value) != _V6_SNAPSHOT_KEYS:
            raise ResearchRepositoryError(
                "v6_snapshot_keys_invalid", "v6 snapshot exact keys differ"
            )
        if (
            value.get("schema_version") != 1
            or value.get("run_id") != parent_run_id
            or value.get("workflow_name") != "deep_research"
            or value.get("workflow_version") != "v6"
        ):
            raise ResearchRepositoryError(
                "v6_snapshot_identity_invalid", "v6 snapshot identity differs"
            )
        semantic = dict(value)
        snapshot_id = semantic.pop("snapshot_id", None)
        snapshot_hash = semantic.pop("snapshot_hash", None)
        expected = _sha256_text(_canonical(semantic))
        if snapshot_hash != expected or snapshot_id != "rcs_" + expected[:24]:
            raise ResearchRepositoryError(
                "v6_snapshot_hash_invalid", "v6 snapshot semantic hash differs"
            )
        policies = value.get("policy_refs")
        if not isinstance(policies, dict) or set(policies) != _V6_POLICY_KEYS:
            raise ResearchRepositoryError(
                "v6_snapshot_policy_invalid", "v6 snapshot policy refs differ"
            )
        lists = ("fact_batch_refs", "provenance_refs", "closure_refs")
        if any(not isinstance(value.get(name), list) for name in lists):
            raise ResearchRepositoryError(
                "v6_snapshot_refs_invalid", "v6 snapshot refs are not arrays"
            )
        batches_raw = [str(item) for item in value["fact_batch_refs"]]
        provenance = [str(item) for item in value["provenance_refs"]]
        if not batches_raw or len(batches_raw) != len(set(batches_raw)):
            raise ResearchRepositoryError(
                "v6_snapshot_refs_invalid", "v6 fact batch refs are empty or repeated"
            )
        if provenance != sorted(set(provenance)):
            raise ResearchRepositoryError(
                "v6_snapshot_refs_invalid", "v6 provenance refs are not sorted unique"
            )
        expected_closure = {
            str(value["spec_ref"]),
            str(value["assessment_ref"]),
            str(value["claim_batch_ref"]),
            *[str(item) for item in value["fact_batch_refs"]],
            *[str(item) for item in value["provenance_refs"]],
            *[str(item) for item in policies.values()],
        }
        closure = [str(item) for item in value["closure_refs"]]
        if closure != sorted(set(closure)) or set(closure) != expected_closure:
            raise ResearchRepositoryError(
                "v6_snapshot_closure_invalid", "v6 snapshot closure is not exact"
            )
        for field in ("spec_ref", "assessment_ref", "claim_batch_ref"):
            _wire_digest(value[field], field)
        for ref in closure:
            _wire_digest(ref, "closure_ref")

        top_level = {
            str(value["spec_ref"]),
            str(value["assessment_ref"]),
            str(value["claim_batch_ref"]),
            *batches_raw,
        }
        policy_values = {str(ref) for ref in policies.values()}
        if top_level & policy_values or set(provenance) & (top_level | policy_values):
            raise ResearchRepositoryError(
                "v6_snapshot_ref_class_invalid",
                "v6 snapshot places a registered ref in the wrong class",
            )

        async def read_json(ref: object, field: str) -> dict[str, Any]:
            _, item = await self._read_registered_json_v6(db, ref, field=field)
            return item

        try:
            spec = ResearchSpecV1.from_json(await read_json(value["spec_ref"], "spec_ref"))
            assessment_value = await read_json(value["assessment_ref"], "assessment_ref")
            assessment = AnswerAssessmentV1.from_json(assessment_value)
            claim_value = await read_json(value["claim_batch_ref"], "claim_batch_ref")
            claim = ClaimBatchV1.from_json(claim_value)
        except (TypeError, ValueError) as exc:
            raise ResearchRepositoryError(
                "v6_snapshot_object_invalid", "v6 snapshot top-level object is invalid"
            ) from exc

        policy_objects: dict[str, dict[str, Any]] = {}
        for policy_name, policy_ref in policies.items():
            policy = await read_json(policy_ref, f"{policy_name}_policy_ref")
            # Production v6 route-policy-v1 predates the generic policy
            # ``schema_version`` field and encodes its version in the immutable
            # policy_id.  Those content-addressed bytes may already be reachable
            # from completed runs, so restart recovery must recognize that one
            # exact historical shape.  Every other policy remains strict v1.
            legacy_route_v1 = (
                policy_name == "route"
                and set(policy) == _V6_ROUTE_POLICY_V1_KEYS
                and policy.get("policy_id") == "deep-research-v6-official-retrieval-v1"
                and all(
                    isinstance(policy.get(name), int)
                    and not isinstance(policy.get(name), bool)
                    and int(policy[name]) > 0
                    for name in (
                        "parent_deadline_ms",
                        "page_deadline_ms",
                        "max_queries",
                        "max_fetches",
                        "max_fetch_concurrency",
                        "max_lanes",
                    )
                )
                and isinstance(policy.get("official_source_policy_hash"), str)
                and len(str(policy["official_source_policy_hash"])) == 64
                and not set(str(policy["official_source_policy_hash"])) - set("0123456789abcdef")
            )
            if policy.get("schema_version") != 1 and not legacy_route_v1:
                raise ResearchRepositoryError(
                    "v6_snapshot_policy_invalid", f"{policy_name} policy version differs"
                )
            policy_objects[str(policy_name)] = policy

        batches: list[Any] = []
        previous_head = GENESIS_EVIDENCE_HEAD
        ordered_fact_refs: list[str] = []
        ordered_inference_refs: list[str] = []
        expected_kinds: dict[str, str] = {}

        def expect(ref: object, kind: str) -> None:
            wire = str(ref)
            _wire_digest(wire, f"{kind}_ref")
            existing = expected_kinds.get(wire)
            if existing is not None and existing != kind:
                raise ResearchRepositoryError(
                    "v6_snapshot_ref_class_invalid",
                    f"one v6 ref is classified as both {existing} and {kind}",
                )
            expected_kinds[wire] = kind

        for ordinal, batch_ref in enumerate(batches_raw):
            try:
                batch = EvidenceFactBatchV1.from_json(
                    await read_json(batch_ref, f"fact_batch_refs[{ordinal}]")
                )
            except (TypeError, ValueError) as exc:
                raise ResearchRepositoryError(
                    "v6_snapshot_batch_invalid", "v6 evidence batch is invalid"
                ) from exc
            if (
                batch.ordinal != ordinal
                or batch.previous_head_hash != previous_head
                or batch.spec_hash != value["spec_hash"]
                or batch.policy_refs != policies
            ):
                raise ResearchRepositoryError(
                    "v6_snapshot_batch_chain_invalid", "v6 evidence batch chain differs"
                )
            previous_head = batch.head_hash
            batches.append(batch)
            ordered_fact_refs.extend(batch.admitted_fact_refs)
            ordered_inference_refs.extend(batch.registered_inference_refs)
            for ref in batch.page_result_refs:
                expect(ref, "page")
            for ref in batch.admitted_fact_refs:
                expect(ref, "fact")
            for ref in batch.registered_inference_refs:
                expect(ref, "inference")
            for slot in batch.candidate_slot_results:
                expect(slot["producer_outcome_ref"], "producer")
                if slot["bundle_ref"] is not None:
                    expect(slot["bundle_ref"], "candidate_bundle")
            for slot in batch.inference_slot_results:
                expect(slot["effect_outcome_ref"], "llm_outcome")
                if slot["proposal_bundle_ref"] is not None:
                    expect(slot["proposal_bundle_ref"], "inference_bundle")

        if batches[-1].run_id != parent_run_id or previous_head != value["evidence_head_hash"]:
            raise ResearchRepositoryError(
                "v6_snapshot_batch_chain_invalid", "v6 final evidence head differs"
            )
        assessment_policy_hash = _wire_digest(policies["assessment"], "assessment_policy_ref")
        expected_input_hash = derive_assessment_input_hash(
            spec_hash=str(value["spec_hash"]),
            evidence_head_hash=str(value["evidence_head_hash"]),
            ordered_fact_refs=ordered_fact_refs,
            ordered_inference_refs=ordered_inference_refs,
            assessment_policy_hash=assessment_policy_hash,
        )
        if any(
            (
                spec.spec_hash != value["spec_hash"],
                assessment.spec_hash != value["spec_hash"],
                assessment.evidence_head_hash != value["evidence_head_hash"],
                assessment.assessment_hash != value["assessment_hash"],
                assessment.assessment_input_hash != value["assessment_input_hash"],
                assessment.assessment_input_hash != expected_input_hash,
                assessment.policy_hash != assessment_policy_hash,
                claim.run_id != parent_run_id,
                claim.spec_hash != value["spec_hash"],
                claim.evidence_head_hash != value["evidence_head_hash"],
                claim.assessment_hash != value["assessment_hash"],
                claim.claim_policy_hash != _wire_digest(policies["claim"], "claim_policy_ref"),
            )
        ):
            raise ResearchRepositoryError(
                "v6_snapshot_cross_hash_invalid", "v6 snapshot semantic cross-hash differs"
            )

        async def decode_expected(ref: str, kind: str, item: Mapping[str, Any]) -> None:
            if kind == "page":
                PageExtractionResultV1.from_json(item)
            elif kind == "fact":
                AdmittedResearchFactV1.from_json(item)
            elif kind == "inference":
                RegisteredInferenceV1.from_json(item)
            elif kind == "producer":
                CandidateProducerOutcomeV1.from_json(item)
            elif kind == "inference_bundle":
                InferenceProposalBundleV1.from_json(item)
            elif kind == "llm_outcome":
                ResearchLLMEffectOutcomeV1.from_json(item)
            elif kind == "candidate_bundle":
                page_ref = item.get("page_result_ref")
                page_value = await read_json(page_ref, "candidate_bundle.page_result_ref")
                page = PageExtractionResultV1.from_json(page_value)
                body = b""
                if page.page_record is not None:
                    _, body = await self._read_registered_bytes_v6(
                        db, page.page_record["body_ref"], field="candidate_bundle.body_ref"
                    )
                EvidenceCandidateBundleV1.from_json(item, body_bytes=body)
            else:  # pragma: no cover - expect() owns this closed set
                raise AssertionError(kind)

        def decode_known(item: Mapping[str, Any]) -> None:
            """Strictly decode every registered v6 object kind with a public decoder."""

            if "route_id" in item:
                RouteDecisionV1.from_json(item)
            elif "locator_id" in item:
                SourceLocatorV1.from_json(item)
            elif "outcome_id" in item and "ordinal" in item and "canonical_effect_id" in item:
                PageAttemptOutcomeV1.from_json(item)
            elif "repair_id" in item:
                EvidenceRepairRequestV1.from_json(item)
            elif "result_id" in item and "logical_page_id" in item and "page_record" in item:
                PageExtractionResultV1.from_json(item)
            elif "result_id" in item and "candidates" in item and "logical_effect_id" in item:
                OfficialSearchResultV1.from_json(item)
            elif "fact_id" in item:
                AdmittedResearchFactV1.from_json(item)
            elif "inference_id" in item:
                RegisteredInferenceV1.from_json(item)
            elif "outcome_id" in item and "origin" in item:
                CandidateProducerOutcomeV1.from_json(item)
            elif "outcome_id" in item and "result_kind" in item and "logical_effect_id" in item:
                ResearchLLMEffectOutcomeV1.from_json(item)
            elif "bundle_id" in item and "proposals" in item:
                InferenceProposalBundleV1.from_json(item)

        roots = set(expected_kinds)
        roots.update(_walk_v6_wire_refs(assessment_value) - policy_values)
        roots.update(_walk_v6_wire_refs(claim_value) - policy_values)
        seen: set[str] = set()
        pending = sorted(roots)
        while pending:
            ref = pending.pop(0)
            if ref in seen or ref in policy_values:
                continue
            if ref in top_level:
                raise ResearchRepositoryError(
                    "v6_snapshot_ref_class_invalid", "v6 provenance reaches a top-level object"
                )
            _, data = await self._read_registered_bytes_v6(db, ref, field="provenance_ref")
            seen.add(ref)
            try:
                item = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict) or _canonical(item).encode("utf-8") != data:
                raise ResearchRepositoryError(
                    "v6_provenance_json_invalid", "v6 provenance JSON is not canonical"
                )
            try:
                kind = expected_kinds.get(ref)
                if kind is not None:
                    await decode_expected(ref, kind, item)
                else:
                    decode_known(item)
            except (TypeError, ValueError) as exc:
                raise ResearchRepositoryError(
                    "v6_provenance_object_invalid", "v6 provenance object is invalid"
                ) from exc
            for child in sorted(_walk_v6_wire_refs(item) - policy_values - seen):
                _wire_digest(child, "provenance_child_ref")
                pending.append(child)

        if seen != set(provenance):
            raise ResearchRepositoryError(
                "v6_snapshot_provenance_invalid",
                "v6 provenance is not the exact transitive closure",
            )

    async def persist_v6_continuation_snapshot(
        self,
        *,
        run_id: str,
        operation_id: str,
        terminal_manifest_ref: str,
        continue_until: float,
        pin_id: str | None = None,
    ) -> tuple[str, bool]:
        """Persist the v6 semantic snapshot row and live parent pin atomically.

        ``workflow_research_snapshots.manifest_ref`` intentionally stores the bare
        snapshot blob digest for v6; v5 rows retain their legacy manifest meaning.
        """

        now = float(self._clock())
        if continue_until <= now:
            raise ResearchRepositoryError("snapshot_expiry_invalid", "snapshot pin must be live")
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run = await (
                await db.execute(
                    "SELECT workflow_name,workflow_version,status FROM workflow_runs WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
            if (
                run is None
                or run["workflow_name"] != "deep_research"
                or run["workflow_version"] != "v6"
                or run["status"] != "completed"
            ):
                raise ResearchRepositoryError(
                    "parent_not_continuable", "snapshot owner is not completed deep_research/v6"
                )
            manifest_digest, manifest = await self._read_registered_json_v6(
                db, terminal_manifest_ref, field="terminal_manifest_ref"
            )

            try:
                decoded_manifest = TerminalDeliveryManifestV1.from_json(manifest)
            except (TypeError, ValueError) as exc:
                raise ResearchRepositoryError(
                    "v6_manifest_invalid", "terminal manifest is invalid"
                ) from exc
            if (
                decoded_manifest.manifest_hash != manifest_digest
                or manifest.get("run_id") != run_id
                or manifest.get("workflow_version") != "v6"
                or manifest.get("engine_terminal")
                != {"status": "completed", "error_code": None, "recovery_action": None}
            ):
                raise ResearchRepositoryError(
                    "v6_manifest_identity_invalid", "terminal manifest identity differs"
                )
            if manifest.get("answer_status") not in _CONTINUABLE_DELIVERY:
                raise ResearchRepositoryError(
                    "parent_delivery_not_continuable",
                    "completed evidence answer does not admit a continuation",
                )
            snapshot_digest, snapshot = await self._read_registered_json_v6(
                db, manifest.get("continuation_snapshot_ref"), field="continuation_snapshot_ref"
            )
            await self._validate_v6_snapshot_graph(db, snapshot, parent_run_id=run_id)
            snapshot_hash = str(snapshot["snapshot_hash"])
            if manifest.get("continuation_snapshot_hash") != snapshot_hash:
                raise ResearchRepositoryError(
                    "v6_snapshot_manifest_mismatch", "manifest snapshot hash differs"
                )
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_research_snapshots WHERE snapshot_hash=?",
                    (snapshot_hash,),
                )
            ).fetchone()
            created = existing is None
            if existing is not None and (
                existing["run_id"] != run_id
                or existing["operation_id"] != operation_id
                or int(existing["schema_version"]) != 1
                or existing["manifest_ref"] != snapshot_digest
                or float(existing["expires_at"]) != continue_until
            ):
                raise ResearchRepositoryError("snapshot_conflict", "v6 snapshot row conflicts")
            if existing is None:
                await db.execute(
                    """INSERT INTO workflow_research_snapshots(
                    snapshot_hash,run_id,operation_id,schema_version,manifest_ref,created_at,expires_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (snapshot_hash, run_id, operation_id, 1, snapshot_digest, now, continue_until),
                )
            for digest in sorted(
                {
                    manifest_digest,
                    snapshot_digest,
                    *(_wire_digest(ref, "closure_ref") for ref in snapshot["closure_refs"]),
                }
            ):
                await db.execute(
                    """INSERT OR IGNORE INTO workflow_blob_refs(
                    sha256,owner_kind,owner_id,created_at) VALUES(?,'research_snapshot',?,?)""",
                    (digest, snapshot_hash, now),
                )
            actual_pin_id = pin_id or _sha256_text(f"{snapshot_hash}|{run_id}|continue_parent")
            await db.execute(
                """INSERT INTO workflow_research_snapshot_pins(
                pin_id,snapshot_hash,run_id,pin_kind,expires_at,created_at
                ) VALUES(?,?,?,'continue_parent',?,?)
                ON CONFLICT(snapshot_hash,run_id,pin_kind) DO NOTHING""",
                (actual_pin_id, snapshot_hash, run_id, continue_until, now),
            )
            pin = await (
                await db.execute(
                    """SELECT expires_at FROM workflow_research_snapshot_pins
                    WHERE snapshot_hash=? AND run_id=? AND pin_kind='continue_parent'""",
                    (snapshot_hash, run_id),
                )
            ).fetchone()
            if pin is None or float(pin["expires_at"]) != continue_until:
                raise ResearchRepositoryError("snapshot_pin_conflict", "v6 parent pin conflicts")
            await db.commit()
            return snapshot_hash, created
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def _v6_audit_result(
        self,
        db: aiosqlite.Connection,
        *,
        parent_run_id: str,
        caller_idempotency_key: str,
        child_run_id: str,
        child_operation_id: str,
        created: bool,
        now: float,
    ) -> tuple[str, bool]:
        if not caller_idempotency_key:
            raise ResearchRepositoryError(
                "continuation_audit_key_invalid", "caller idempotency key is empty"
            )
        caller_hash = _sha256_text(caller_idempotency_key)
        audit_operation_id = _sha256_text(
            "deep-research-v6-continuation-audit-v1|" + parent_run_id + "|" + caller_idempotency_key
        )
        request = {
            "schema_version": 1,
            "parent_run_id": parent_run_id,
            "caller_idempotency_key_hash": caller_hash,
        }
        request_hash = _sha256_text(_canonical(request))
        result = {
            "schema_version": 1,
            "parent_run_id": parent_run_id,
            "child_run_id": child_run_id,
            "child_operation_id": child_operation_id,
            "created": created,
        }
        existing = await (
            await db.execute(
                "SELECT * FROM workflow_operations WHERE operation_id=?", (audit_operation_id,)
            )
        ).fetchone()
        if existing is not None:
            try:
                stored = json.loads(str(existing["result_json"]))
            except json.JSONDecodeError as exc:
                raise ResearchRepositoryError(
                    "continuation_audit_conflict", "audit result is corrupt"
                ) from exc
            if (
                existing["run_id"] != child_run_id
                or existing["operation_kind"] != "research_continue_audit_v1"
                or existing["request_hash"] != request_hash
                or not isinstance(stored, dict)
                or set(stored) != set(result)
                or any(stored.get(key) != result[key] for key in result if key != "created")
                or not isinstance(stored.get("created"), bool)
            ):
                raise ResearchRepositoryError(
                    "continuation_audit_conflict", "audit identity conflicts"
                )
            return audit_operation_id, bool(stored["created"])
        await db.execute(
            """INSERT INTO workflow_operations(
            operation_id,run_id,operation_kind,request_hash,result_json,created_at
            ) VALUES(?,?,'research_continue_audit_v1',?,?,?)""",
            (audit_operation_id, child_run_id, request_hash, _canonical(result), now),
        )
        return audit_operation_id, created
