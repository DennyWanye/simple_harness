"""Deep-research v6 continuations, snapshot pins and lineage reachability."""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aiosqlite

from ...definitions.deep_research_v6_delivery import TerminalDeliveryManifestV1
from .core import ResearchRepositoryCore
from .support import (
    _CONTINUABLE_DELIVERY,
    ResearchRepositoryError,
    _canonical,
    _sha256_text,
    _wire_digest,
)

_V6_CONTINUATION_NAMESPACE = uuid.UUID("a7e5f48a-9b2b-549f-a67c-3ebdee7b9c78")
_V6_POLICY_VERSION = 1


@dataclass(frozen=True, slots=True)
class ResearchReachability:
    protected_run_ids: frozenset[str]
    protected_snapshot_hashes: frozenset[str]
    protected_pin_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class ContinuationCreateResultV1:
    schema_version: int
    parent_run_id: str
    child_run_id: str
    child_operation_id: str
    created: bool
    start_payload: dict[str, Any]
    start_request_hash: str
    audit_operation_id: str

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "parent_run_id": self.parent_run_id,
            "child_run_id": self.child_run_id,
            "child_operation_id": self.child_operation_id,
            "created": self.created,
            "start_payload": copy.deepcopy(self.start_payload),
            "start_request_hash": self.start_request_hash,
            "audit_operation_id": self.audit_operation_id,
        }


def _v6_continuation_identity(parent_run_id: str) -> dict[str, str]:
    stable = uuid.uuid5(
        _V6_CONTINUATION_NAMESPACE,
        _canonical({"parent_run_id": parent_run_id, "policy_version": _V6_POLICY_VERSION}),
    )
    child_run_id = str(stable)
    child_operation_id = f"research:{child_run_id}"
    child_request_key = f"research-continuation-v6:{parent_run_id}:1"
    return {
        "child_run_id": child_run_id,
        "child_operation_id": child_operation_id,
        "child_start_operation_id": f"{child_operation_id}:start",
        "child_request_key": child_request_key,
        "child_request_id": f"continue:v6:{child_run_id}",
        "child_turn_id": uuid.uuid5(stable, "turn").hex,
        "child_trace_id": uuid.uuid5(stable, "trace").hex,
        "child_thread_id": uuid.uuid5(stable, "thread").hex,
        "budget_lease_id": uuid.uuid5(stable, "budget").hex,
    }


class V6ContinuationMixin(ResearchRepositoryCore):
    """Deep-research v6 continuations, snapshot pins and lineage reachability."""

    __slots__ = ()

    async def _validate_existing_continuation_v6(
        self,
        db: aiosqlite.Connection,
        *,
        parent_run_id: str,
        identity: Mapping[str, str],
        head: aiosqlite.Row,
    ) -> tuple[dict[str, Any], str]:
        child_run_id = identity["child_run_id"]
        child_operation_id = identity["child_operation_id"]
        if (
            head["parent_run_id"] != parent_run_id
            or head["child_run_id"] != child_run_id
            or head["child_operation_id"] != child_operation_id
            or int(head["policy_version"]) != _V6_POLICY_VERSION
        ):
            raise ResearchRepositoryError(
                "continuation_head_conflict", "continuation head identity differs"
            )
        child = await (
            await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (child_run_id,))
        ).fetchone()
        if child is None or any(
            (
                child["parent_run_id"] != parent_run_id,
                child["workflow_name"] != "deep_research",
                child["workflow_version"] != "v6",
                child["trace_id"] != identity["child_trace_id"],
                child["thread_id"] != identity["child_thread_id"],
                child["request_id"] != identity["child_request_id"],
                child["turn_id"] != identity["child_turn_id"],
            )
        ):
            raise ResearchRepositoryError(
                "continuation_child_conflict", "continuation child identity differs"
            )
        lineage = await (
            await db.execute(
                "SELECT * FROM workflow_research_lineage WHERE operation_id=? AND run_id=?",
                (child_operation_id, child_run_id),
            )
        ).fetchone()
        if lineage is None or any(
            (
                lineage["parent_run_id"] != parent_run_id,
                lineage["parent_operation_id"] != head["parent_operation_id"],
                lineage["snapshot_hash"] != head["source_snapshot_hash"],
                lineage["budget_lease_id"] != identity["budget_lease_id"],
            )
        ):
            raise ResearchRepositoryError(
                "continuation_lineage_conflict", "continuation lineage differs"
            )
        start = await (
            await db.execute(
                "SELECT * FROM workflow_operations WHERE operation_id=?",
                (identity["child_start_operation_id"],),
            )
        ).fetchone()
        if (
            start is None
            or start["run_id"] != child_run_id
            or start["operation_kind"] != "research_continue_start_v1"
        ):
            raise ResearchRepositoryError(
                "continuation_start_missing", "continuation start operation differs"
            )
        try:
            start_payload = json.loads(str(start["result_json"]))
        except json.JSONDecodeError as exc:
            raise ResearchRepositoryError(
                "continuation_start_invalid", "continuation start payload is corrupt"
            ) from exc
        expected_payload = {
            "schema_version": 1,
            "parent_run_id": parent_run_id,
            "source_snapshot_hash": str(head["source_snapshot_hash"]),
        }
        expected_hash = _sha256_text(_canonical(expected_payload))
        if start_payload != expected_payload or start["request_hash"] != expected_hash:
            raise ResearchRepositoryError(
                "continuation_start_conflict", "continuation start payload differs"
            )
        start_request = await (
            await db.execute(
                "SELECT * FROM workflow_start_requests WHERE request_key=?",
                (identity["child_request_key"],),
            )
        ).fetchone()
        if start_request is None or any(
            (
                start_request["run_id"] != child_run_id,
                start_request["request_id"] != identity["child_request_id"],
                start_request["turn_id"] != identity["child_turn_id"],
                start_request["workflow_name"] != "deep_research",
                start_request["capability_hash"] != child["capability_hash"],
            )
        ):
            raise ResearchRepositoryError(
                "continuation_start_request_conflict", "continuation start request differs"
            )
        capability = await (
            await db.execute(
                "SELECT snapshot_json FROM workflow_capabilities WHERE capability_hash=?",
                (child["capability_hash"],),
            )
        ).fetchone()
        try:
            capability_value = json.loads(str(capability["snapshot_json"])) if capability else None
            start_meta = (
                capability_value.get("_workflow_start")
                if isinstance(capability_value, dict)
                else None
            )
            capability_identity = (
                start_meta.get("identity") if isinstance(start_meta, dict) else None
            )
        except json.JSONDecodeError:
            capability_identity = None
        if not isinstance(capability_identity, dict) or any(
            (
                capability_identity.get("request_id") != identity["child_request_id"],
                capability_identity.get("turn_id") != identity["child_turn_id"],
                capability_identity.get("logical_slot") != identity["child_request_key"],
            )
        ):
            raise ResearchRepositoryError(
                "continuation_capability_conflict", "continuation capability differs"
            )
        refs = await (
            await db.execute(
                "SELECT session_kind,session_id,session_epoch FROM workflow_session_refs WHERE run_id=? AND deleted_at IS NULL",
                (child_run_id,),
            )
        ).fetchall()
        if not refs or len({str(row["session_kind"]) for row in refs}) != len(refs):
            raise ResearchRepositoryError(
                "continuation_session_refs_invalid", "continuation session refs differ"
            )
        return expected_payload, expected_hash

    async def create_or_get_continuation_v6(
        self, parent_run_id: str, caller_idempotency_key: str
    ) -> ContinuationCreateResultV1:
        """Atomically claim the one policy-v1 v6 continuation of ``parent_run_id``."""

        if not parent_run_id or not caller_idempotency_key:
            raise ResearchRepositoryError(
                "continuation_request_invalid", "continuation identity is empty"
            )
        identity = _v6_continuation_identity(parent_run_id)
        child_run_id = identity["child_run_id"]
        child_operation_id = identity["child_operation_id"]
        now = float(self._clock())
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            head = await (
                await db.execute(
                    "SELECT * FROM workflow_research_continuation_heads WHERE parent_run_id=?",
                    (parent_run_id,),
                )
            ).fetchone()
            if head is not None:
                start_payload, start_hash = await self._validate_existing_continuation_v6(
                    db, parent_run_id=parent_run_id, identity=identity, head=head
                )
                audit_id, stored_created = await self._v6_audit_result(
                    db,
                    parent_run_id=parent_run_id,
                    caller_idempotency_key=caller_idempotency_key,
                    child_run_id=child_run_id,
                    child_operation_id=child_operation_id,
                    created=False,
                    now=now,
                )
                await self._fault("after_audit_operation_insert")
                await self._fault("before_commit")
                await db.commit()
                return ContinuationCreateResultV1(
                    1,
                    parent_run_id,
                    child_run_id,
                    child_operation_id,
                    stored_created,
                    start_payload,
                    start_hash,
                    audit_id,
                )

            parent = await (
                await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (parent_run_id,))
            ).fetchone()
            if parent is None or any(
                (
                    parent["workflow_name"] != "deep_research",
                    parent["workflow_version"] != "v6",
                    parent["status"] != "completed",
                    parent["head_checkpoint_id"] is None,
                )
            ):
                raise ResearchRepositoryError(
                    "parent_not_continuable", "parent is not a terminal deep_research/v6 run"
                )
            parent_lineage = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE run_id=?", (parent_run_id,)
                )
            ).fetchone()
            snapshot, snapshot_hash, spec_digest = await self._load_v6_continuation_snapshot(
                db,
                now=now,
                parent=parent,
                parent_lineage=parent_lineage,
                parent_run_id=parent_run_id,
            )

            (
                child_capability_hash,
                start_hash,
                start_payload,
            ) = await self._v6_continuation_start_payload(
                child_run_id,
                db=db,
                identity=identity,
                now=now,
                parent=parent,
                parent_run_id=parent_run_id,
                snapshot_hash=snapshot_hash,
            )
            audit_id, stored_created = await self._v6_audit_result(
                db,
                parent_run_id=parent_run_id,
                caller_idempotency_key=caller_idempotency_key,
                child_run_id=child_run_id,
                child_operation_id=child_operation_id,
                created=True,
                now=now,
            )
            await self._fault("after_audit_operation_insert")
            await db.execute(
                """INSERT INTO workflow_start_requests(
                request_key,session_id,request_id,turn_id,workflow_name,capability_hash,run_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    identity["child_request_key"],
                    parent["session_id"],
                    identity["child_request_id"],
                    identity["child_turn_id"],
                    "deep_research",
                    child_capability_hash,
                    child_run_id,
                    now,
                ),
            )
            await self._record_v6_continuation_refs(
                child_operation_id,
                child_run_id=child_run_id,
                db=db,
                identity=identity,
                now=now,
                parent_lineage=parent_lineage,
                parent_run_id=parent_run_id,
                snapshot=snapshot,
                snapshot_hash=snapshot_hash,
                spec_digest=spec_digest,
                start_hash=start_hash,
                start_payload=start_payload,
            )
            return ContinuationCreateResultV1(
                1,
                parent_run_id,
                child_run_id,
                child_operation_id,
                stored_created,
                start_payload,
                start_hash,
                audit_id,
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def _load_v6_continuation_snapshot(
        self, db, *, now, parent, parent_lineage, parent_run_id: str
    ):
        if parent_lineage is None:
            raise ResearchRepositoryError("parent_lineage_missing", "parent lineage is missing")
        events = await (
            await db.execute(
                """SELECT payload_json FROM workflow_events
                    WHERE run_id=? AND event_type='workflow.final' ORDER BY seq DESC""",
                (parent_run_id,),
            )
        ).fetchall()
        terminal_payload: dict[str, Any] | None = None
        for event in events:
            try:
                candidate = json.loads(str(event["payload_json"]))
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and candidate.get("manifest_ref"):
                terminal_payload = candidate
                break
        if terminal_payload is None:
            raise ResearchRepositoryError(
                "v6_terminal_event_missing", "parent terminal manifest event is missing"
            )
        manifest_digest, manifest = await self._read_registered_json_v6(
            db, terminal_payload["manifest_ref"], field="terminal_manifest_ref"
        )

        try:
            decoded_manifest = TerminalDeliveryManifestV1.from_json(manifest)
        except (TypeError, ValueError) as exc:
            raise ResearchRepositoryError(
                "v6_manifest_invalid", "terminal manifest is invalid"
            ) from exc
        if (
            decoded_manifest.manifest_hash != manifest_digest
            or manifest.get("run_id") != parent_run_id
            or manifest.get("workflow_name") != "deep_research"
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
                "parent answer status does not admit a continuation",
            )
        snapshot_digest, snapshot = await self._read_registered_json_v6(
            db, manifest.get("continuation_snapshot_ref"), field="continuation_snapshot_ref"
        )
        await self._validate_v6_snapshot_graph(db, snapshot, parent_run_id=parent_run_id)
        snapshot_hash = str(snapshot["snapshot_hash"])
        if manifest.get("continuation_snapshot_hash") != snapshot_hash:
            raise ResearchRepositoryError(
                "v6_snapshot_manifest_mismatch", "manifest snapshot pointer differs"
            )
        snapshot_row = await (
            await db.execute(
                """SELECT * FROM workflow_research_snapshots
                    WHERE snapshot_hash=? AND run_id=? AND operation_id=?""",
                (snapshot_hash, parent_run_id, str(parent_lineage["operation_id"])),
            )
        ).fetchone()
        if (
            snapshot_row is None
            or snapshot_row["manifest_ref"] != snapshot_digest
            or snapshot_row["expires_at"] is None
            or float(snapshot_row["expires_at"]) <= now
        ):
            raise ResearchRepositoryError(
                "snapshot_expired", "v6 continuation snapshot row is missing or expired"
            )
        await self._validate_v6_continuation_refs(
            db,
            manifest_digest=manifest_digest,
            parent=parent,
            parent_run_id=parent_run_id,
            snapshot=snapshot,
            snapshot_digest=snapshot_digest,
        )
        spec_digest, spec = await self._read_registered_json_v6(
            db, snapshot["spec_ref"], field="spec_ref"
        )
        _, assessment = await self._read_registered_json_v6(
            db, snapshot["assessment_ref"], field="assessment_ref"
        )
        _, claim = await self._read_registered_json_v6(
            db, snapshot["claim_batch_ref"], field="claim_batch_ref"
        )
        batches = list(snapshot["fact_batch_refs"])
        if not batches:
            raise ResearchRepositoryError(
                "v6_fact_head_missing", "snapshot has no evidence batches"
            )
        _, final_batch = await self._read_registered_json_v6(
            db, batches[-1], field="fact_batch_ref"
        )
        if any(
            (
                spec.get("spec_hash") != snapshot["spec_hash"],
                manifest.get("spec_hash") != snapshot["spec_hash"],
                final_batch.get("head_hash") != snapshot["evidence_head_hash"],
                assessment.get("spec_hash") != snapshot["spec_hash"],
                assessment.get("evidence_head_hash") != snapshot["evidence_head_hash"],
                assessment.get("assessment_hash") != snapshot["assessment_hash"],
                assessment.get("assessment_input_hash") != snapshot["assessment_input_hash"],
                manifest.get("assessment_hash") != snapshot["assessment_hash"],
                claim.get("assessment_hash") != snapshot["assessment_hash"],
            )
        ):
            raise ResearchRepositoryError(
                "v6_snapshot_cross_hash_invalid", "snapshot semantic cross-hash differs"
            )
        for policy_name, manifest_field in (
            ("claim", "claim_policy_hash"),
            ("quality", "quality_policy_hash"),
        ):
            _, policy = await self._read_registered_json_v6(
                db, snapshot["policy_refs"][policy_name], field=f"{policy_name}_policy_ref"
            )
            if _sha256_text(_canonical(policy)) != manifest.get(manifest_field):
                raise ResearchRepositoryError(
                    "v6_snapshot_policy_hash_invalid", "snapshot policy hash differs"
                )
        pin = await (
            await db.execute(
                """SELECT 1 FROM workflow_research_snapshot_pins
                    WHERE snapshot_hash=? AND run_id=? AND pin_kind='continue_parent'
                      AND expires_at>?""",
                (snapshot_hash, parent_run_id, now),
            )
        ).fetchone()
        if pin is None:
            raise ResearchRepositoryError(
                "snapshot_expired", "v6 continuation parent pin is missing or expired"
            )
        return snapshot, snapshot_hash, spec_digest

    async def _v6_continuation_start_payload(
        self, child_run_id, *, db, identity, now, parent, parent_run_id: str, snapshot_hash
    ):
        parent_capability = await (
            await db.execute(
                "SELECT snapshot_json FROM workflow_capabilities WHERE capability_hash=?",
                (parent["capability_hash"],),
            )
        ).fetchone()
        if parent_capability is None:
            raise ResearchRepositoryError(
                "parent_start_snapshot_missing", "parent capability is missing"
            )
        try:
            parent_capability_value = json.loads(str(parent_capability["snapshot_json"]))
        except json.JSONDecodeError as exc:
            raise ResearchRepositoryError(
                "parent_start_snapshot_invalid", "parent capability is invalid"
            ) from exc
        if not isinstance(parent_capability_value, dict):
            raise ResearchRepositoryError(
                "parent_start_snapshot_invalid", "parent capability is invalid"
            )
        original_capabilities = {
            str(key): copy.deepcopy(value)
            for key, value in parent_capability_value.items()
            if key != "_workflow_start"
        }
        parent_start = parent_capability_value.get("_workflow_start")
        parent_identity = parent_start.get("identity") if isinstance(parent_start, dict) else None
        if not isinstance(parent_identity, dict):
            parent_identity = {
                "venue": "chat",
                "base_session_id": str(parent["session_id"]),
                "code_session_id": "",
                "delivery_session_id": str(parent["session_id"]),
                "base_epoch": 0,
                "code_epoch": 0,
                "workflow_name": "deep_research",
            }
        child_identity = {
            **copy.deepcopy(parent_identity),
            "request_id": identity["child_request_id"],
            "turn_id": identity["child_turn_id"],
            "logical_slot": identity["child_request_key"],
            "workflow_name": "deep_research",
        }
        start_payload = {
            "schema_version": 1,
            "parent_run_id": parent_run_id,
            "source_snapshot_hash": snapshot_hash,
        }
        start_hash = _sha256_text(_canonical(start_payload))
        capabilities_hash = _sha256_text(_canonical(original_capabilities))
        child_capability = {
            **original_capabilities,
            "_workflow_start": {
                "identity": child_identity,
                "identity_key": _sha256_text(_canonical(child_identity)),
                "request_hash": start_hash,
                "args_hash": start_hash,
                "capability_hash": capabilities_hash,
                "start_payload": copy.deepcopy(start_payload),
            },
        }
        child_capability_json = _canonical(child_capability)
        child_capability_hash = _sha256_text(child_capability_json)
        await db.execute(
            """INSERT OR IGNORE INTO workflow_capabilities(
                capability_hash,snapshot_json,created_at) VALUES(?,?,?)""",
            (child_capability_hash, child_capability_json, now),
        )
        capability_row = await (
            await db.execute(
                "SELECT snapshot_json FROM workflow_capabilities WHERE capability_hash=?",
                (child_capability_hash,),
            )
        ).fetchone()
        if capability_row is None or capability_row["snapshot_json"] != child_capability_json:
            raise ResearchRepositoryError(
                "continuation_capability_conflict", "child capability conflicts"
            )
        await self._fault("after_capability_insert")
        await self._insert_v6_continuation_run(
            child_capability_hash,
            child_run_id=child_run_id,
            db=db,
            identity=identity,
            now=now,
            parent=parent,
            parent_run_id=parent_run_id,
        )
        return child_capability_hash, start_hash, start_payload

    async def _validate_v6_continuation_refs(
        self, db, *, manifest_digest, parent, parent_run_id: str, snapshot, snapshot_digest
    ) -> None:
        refs_to_validate = {
            f"sha256:{manifest_digest}",
            f"sha256:{snapshot_digest}",
            *[str(ref) for ref in snapshot["closure_refs"]],
        }
        terminal_checkpoint_id = str(parent["head_checkpoint_id"])
        for wire_ref in sorted(refs_to_validate):
            digest, _ = await self._read_registered_bytes_v6(
                db, wire_ref, field="snapshot_closure_ref"
            )
            owner = await (
                await db.execute(
                    """SELECT 1 FROM workflow_blob_refs r
                        JOIN workflow_checkpoint_owners o ON o.checkpoint_id=r.owner_id
                        WHERE r.sha256=? AND r.owner_kind='checkpoint' AND r.owner_id=?
                          AND o.run_id=? AND o.checkpoint_ns=? LIMIT 1""",
                    (
                        digest,
                        terminal_checkpoint_id,
                        parent_run_id,
                        str(parent["head_checkpoint_ns"]),
                    ),
                )
            ).fetchone()
            if owner is None:
                raise ResearchRepositoryError(
                    "v6_closure_owner_invalid",
                    "snapshot ref lacks parent terminal checkpoint owner",
                )

    async def _insert_v6_continuation_run(
        self, child_capability_hash, *, child_run_id, db, identity, now, parent, parent_run_id: str
    ) -> None:
        await db.execute(
            """INSERT INTO workflow_runs(
                run_id,trace_id,thread_id,checkpoint_ns,head_checkpoint_ns,head_checkpoint_id,
                parent_run_id,source_checkpoint_id,session_id,request_id,turn_id,
                workflow_name,workflow_version,manifest_hash,implementation_hash,capability_hash,
                state_schema_version,status,active_nodes_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,NULL,?,?,?,?,?,'deep_research','v6',?,?,?,?,
                'created','[]',?,?)""",
            (
                child_run_id,
                identity["child_trace_id"],
                identity["child_thread_id"],
                parent["checkpoint_ns"],
                parent["checkpoint_ns"],
                parent_run_id,
                parent["head_checkpoint_id"],
                parent["session_id"],
                identity["child_request_id"],
                identity["child_turn_id"],
                parent["manifest_hash"],
                parent["implementation_hash"],
                child_capability_hash,
                parent["state_schema_version"],
                now,
                now,
            ),
        )
        await self._fault("after_run_insert")

    async def _record_v6_continuation_refs(
        self,
        child_operation_id,
        *,
        child_run_id,
        db,
        identity,
        now,
        parent_lineage,
        parent_run_id: str,
        snapshot,
        snapshot_hash,
        spec_digest,
        start_hash,
        start_payload,
    ) -> None:
        await self._fault("after_start_request_insert")
        await db.execute(
            """INSERT INTO workflow_session_refs(
                run_id,session_kind,session_id,session_epoch,deleted_at)
                SELECT ?,session_kind,session_id,session_epoch,NULL FROM workflow_session_refs
                WHERE run_id=? AND deleted_at IS NULL""",
            (child_run_id, parent_run_id),
        )
        child_ref_count = await (
            await db.execute(
                "SELECT COUNT(*) FROM workflow_session_refs WHERE run_id=?", (child_run_id,)
            )
        ).fetchone()
        if child_ref_count is None or int(child_ref_count[0]) == 0:
            raise ResearchRepositoryError(
                "continuation_session_refs_invalid", "parent has no live session refs"
            )
        await self._fault("after_session_refs_insert")
        await db.execute(
            """INSERT INTO workflow_research_lineage(
                operation_id,run_id,parent_run_id,parent_operation_id,snapshot_hash,
                parent_report_ref,budget_lease_id,created_at) VALUES(?,?,?,?,?,NULL,?,?)""",
            (
                child_operation_id,
                child_run_id,
                parent_run_id,
                parent_lineage["operation_id"],
                snapshot_hash,
                identity["budget_lease_id"],
                now,
            ),
        )
        await self._fault("after_lineage_insert")
        child_pin_id = _sha256_text(f"{snapshot_hash}|{child_run_id}|continue_child")
        await db.execute(
            """INSERT INTO workflow_research_snapshot_pins(
                pin_id,snapshot_hash,run_id,pin_kind,expires_at,created_at
                ) VALUES(?,?,?,'continue_child',NULL,?)""",
            (child_pin_id, snapshot_hash, child_run_id, now),
        )
        await self._fault("after_snapshot_pin_insert")
        await db.execute(
            """INSERT INTO workflow_operations(
                operation_id,run_id,operation_kind,request_hash,result_json,created_at
                ) VALUES(?,?,'research_continue_start_v1',?,?,?)""",
            (
                identity["child_start_operation_id"],
                child_run_id,
                start_hash,
                _canonical(start_payload),
                now,
            ),
        )
        await self._fault("after_start_operation_insert")
        for wire_ref in snapshot["closure_refs"]:
            await db.execute(
                """INSERT INTO workflow_blob_refs(
                    sha256,owner_kind,owner_id,created_at) VALUES(?,'run_staging',?,?)""",
                (_wire_digest(wire_ref, "closure_ref"), child_run_id, now),
            )
        await self._fault("after_inherited_staging_owners_insert")
        await db.execute(
            """INSERT INTO workflow_research_continuation_heads(
                parent_run_id,child_run_id,parent_operation_id,child_operation_id,
                source_snapshot_hash,spec_hash,spec_blob_digest,evidence_head_hash,
                policy_version,claimed_at) VALUES(?,?,?,?,?,?,?,?,1,?)""",
            (
                parent_run_id,
                child_run_id,
                parent_lineage["operation_id"],
                child_operation_id,
                snapshot_hash,
                snapshot["spec_hash"],
                spec_digest,
                snapshot["evidence_head_hash"],
                now,
            ),
        )
        await self._fault("after_continuation_head_insert")
        await self._fault("before_commit")
        await db.commit()

    async def load_continuation_snapshot_v6(self, child_run_id: str) -> dict[str, Any]:
        """Hydrate an existing child strictly through its claimed head and inherited owners."""

        await self.initialize()
        db = await self._connect()
        try:
            lineage = await (
                await db.execute(
                    """SELECT l.*,h.parent_run_id AS head_parent,h.child_run_id AS head_child
                    FROM workflow_research_lineage l
                    JOIN workflow_research_continuation_heads h
                      ON h.child_operation_id=l.operation_id
                    WHERE l.run_id=?""",
                    (child_run_id,),
                )
            ).fetchone()
            if (
                lineage is None
                or lineage["head_child"] != child_run_id
                or lineage["head_parent"] != lineage["parent_run_id"]
            ):
                raise ResearchRepositoryError(
                    "continuation_head_missing", "child continuation head is missing"
                )
            row = await (
                await db.execute(
                    """SELECT * FROM workflow_research_snapshots
                    WHERE snapshot_hash=? AND run_id=?""",
                    (lineage["snapshot_hash"], lineage["parent_run_id"]),
                )
            ).fetchone()
            if row is None:
                raise ResearchRepositoryError(
                    "continuation_snapshot_missing", "child source snapshot is missing"
                )
            _, snapshot = await self._read_registered_json_v6(
                db, row["manifest_ref"], field="continuation_snapshot_ref"
            )
            await self._validate_v6_snapshot_graph(
                db, snapshot, parent_run_id=str(lineage["parent_run_id"])
            )
            if snapshot["snapshot_hash"] != lineage["snapshot_hash"]:
                raise ResearchRepositoryError(
                    "continuation_snapshot_conflict", "child source snapshot differs"
                )
            for wire_ref in snapshot["closure_refs"]:
                digest = _wire_digest(wire_ref, "closure_ref")
                owner = await (
                    await db.execute(
                        """SELECT 1 FROM workflow_blob_refs
                        WHERE sha256=? AND owner_kind='run_staging' AND owner_id=?""",
                        (digest, child_run_id),
                    )
                ).fetchone()
                if owner is None:
                    raise ResearchRepositoryError(
                        "continuation_owner_missing", "child inherited owner is missing"
                    )
            return copy.deepcopy(snapshot)
        finally:
            await db.close()

    async def settle_run_snapshot_pins(self, run_id: str, *, expires_at: float) -> int:
        """Give non-expiring child pins their terminal-retention expiry."""

        now = float(self._clock())
        if expires_at <= now:
            raise ResearchRepositoryError(
                "pin_expiry_invalid", "terminal pin expiry must be in the future"
            )
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run = await (
                await db.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None or run["status"] not in {"completed", "failed", "cancelled"}:
                raise ResearchRepositoryError(
                    "run_not_terminal", "only terminal run pins can expire"
                )
            cursor = await db.execute(
                """UPDATE workflow_research_snapshot_pins SET expires_at=?
                WHERE run_id=? AND pin_kind='continue_child' AND expires_at IS NULL""",
                (expires_at, run_id),
            )
            await db.commit()
            return cursor.rowcount
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def lineage_reachability(self, *, now: float | None = None) -> ResearchReachability:
        """Recompute protected runs/snapshots/pins from durable roots each pass."""

        instant = float(self._clock() if now is None else now)
        await self.initialize()
        db = await self._connect()
        try:
            roots = await (
                await db.execute(
                    """SELECT run_id FROM workflow_runs WHERE status NOT IN ('completed','failed','cancelled')
                    UNION SELECT run_id FROM workflow_run_control_commands
                      WHERE status IN ('open','accepted','observed','settled')
                    UNION SELECT run_id FROM workflow_research_snapshot_pins
                      WHERE expires_at IS NULL OR expires_at>?""",
                    (instant,),
                )
            ).fetchall()
            protected = {str(row[0]) for row in roots}
            changed = True
            while changed:
                changed = False
                if not protected:
                    break
                rows = await (
                    await db.execute(
                        f"""SELECT run_id,parent_run_id FROM workflow_research_lineage
                        WHERE run_id IN ({",".join("?" for _ in protected)})""",
                        tuple(sorted(protected)),
                    )
                ).fetchall()
                for row in rows:
                    parent = row["parent_run_id"]
                    if parent is not None and str(parent) not in protected:
                        protected.add(str(parent))
                        changed = True
            pin_rows: list[aiosqlite.Row] = []
            if protected:
                pin_rows = await (
                    await db.execute(
                        f"""SELECT pin_id,snapshot_hash FROM workflow_research_snapshot_pins
                        WHERE run_id IN ({",".join("?" for _ in protected)})
                           OR expires_at IS NULL OR expires_at>?""",
                        (*tuple(sorted(protected)), instant),
                    )
                ).fetchall()
            else:
                pin_rows = await (
                    await db.execute(
                        """SELECT pin_id,snapshot_hash FROM workflow_research_snapshot_pins
                        WHERE expires_at IS NULL OR expires_at>?""",
                        (instant,),
                    )
                ).fetchall()
            snapshots = {str(row["snapshot_hash"]) for row in pin_rows}
            if protected:
                lineage_rows = await (
                    await db.execute(
                        f"""SELECT snapshot_hash FROM workflow_research_lineage
                        WHERE run_id IN ({",".join("?" for _ in protected)}) AND snapshot_hash IS NOT NULL""",
                        tuple(sorted(protected)),
                    )
                ).fetchall()
                snapshots.update(str(row[0]) for row in lineage_rows)
            return ResearchReachability(
                protected_run_ids=frozenset(protected),
                protected_snapshot_hashes=frozenset(snapshots),
                protected_pin_ids=frozenset(str(row["pin_id"]) for row in pin_rows),
            )
        finally:
            await db.close()
