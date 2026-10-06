# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501 (fixed SQL statement literals)
"""Internal, exact metadata reads from the original orchestrator Store.

These records are inputs to a validity computation, never access grants. The
caller must supply current authority and root witnesses separately. CAS and
external execution receipts are deliberately not resolved by a metadata read.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..assurance.certificates import UseIdentity
from ..assurance.codec import MAX_BYTES, MAX_RECORD_BYTES, AssuranceError, canonical, decode, fingerprint, text
from ..assurance.event_kinds import EVENT_REF_KINDS, SOURCE_EVENT_SQL
from ..assurance.evidence import ReadItem
from ..assurance.refs import AssuranceRef
from ..assurance.root_gate import CurrentReadPermission
from .assurance_source_inventory import GLOBAL_TABLES, MISSION_TABLES, SOURCE_PRIMARY_KEYS
from .store import Store, StoreConflict

MISSION_EPOCH_SCOPE = "assurance:mission"
READER_VERSION = "assurance-store-read-v1"


@dataclass(frozen=True, slots=True)
class EpochSnapshot:
    mission: int
    environment: int
    clock_generation: int
    wall_high_ms: int
    clock_state: str

    def to_json(self) -> dict[str, Any]:
        return {
            "mission": self.mission,
            "environment": self.environment,
            "clock_generation": self.clock_generation,
            "wall_high_ms": self.wall_high_ms,
            "clock_state": self.clock_state,
        }


def read_epochs_locked(connection: sqlite3.Connection, mission_id: str) -> EpochSnapshot:
    """Requires the original consistent read or write transaction; absence is not zero."""
    if not connection.in_transaction:
        raise AssuranceError("READ_TRANSACTION_REQUIRED")
    mission = connection.execute(
        "SELECT epoch FROM validity_epochs WHERE mission_id=? AND scope_id=?",
        (mission_id, MISSION_EPOCH_SCOPE),
    ).fetchone()
    environment = connection.execute(
        "SELECT * FROM assurance_environment_state WHERE singleton=1"
    ).fetchone()
    if mission is None:
        raise AssuranceError("ASSURANCE_MISSION_EPOCH_UNINITIALIZED")
    if environment is None:
        raise AssuranceError("ASSURANCE_ENVIRONMENT_UNINITIALIZED")
    return EpochSnapshot(
        mission[0],
        environment["epoch"],
        environment["clock_generation"],
        environment["wall_high_ms"],
        environment["clock_state"],
    )


def clock_discontinuous(current: EpochSnapshot, *, now_ms: int) -> bool:
    """时钟不可信：环境时钟不在 STABLE，或"现在"落在已见的高水位之前（回拨）。证书最终锁与收尾
    最终事务用同一个判定（第 1 批 A01）。"""
    return current.clock_state != "STABLE" or now_ms < current.wall_high_ms


def require_epochs_locked(
    connection: sqlite3.Connection, mission_id: str, captured: EpochSnapshot, *, now_ms: int
) -> None:
    """Short final barrier. Access, exact references, root and expiry are separate gates."""
    current = read_epochs_locked(connection, mission_id)
    if (current.mission, current.environment, current.clock_generation) != (
        captured.mission,
        captured.environment,
        captured.clock_generation,
    ):
        raise AssuranceError("RECHECK_REQUIRED")
    if clock_discontinuous(current, now_ms=now_ms):
        raise AssuranceError("TIME_DISCONTINUITY")


CurrentAuthority = Callable[[UseIdentity, AssuranceRef], CurrentReadPermission]


def _permission(
    authority: CurrentAuthority, identity: UseIdentity, ref: AssuranceRef, now_ms: int
) -> CurrentReadPermission:
    """The one judgment of "may this identity read this reference now" (计划 §3.1：解析时核用途
    与访问）。解析器带授权时在解析时调它；最终屏障 :func:`_require_same_permission` 也调它。"""
    permission = authority(identity, ref)
    if not isinstance(permission, CurrentReadPermission):
        raise AssuranceError("ACCESS_POLICY_WITNESS_REQUIRED")
    if now_ms >= permission.not_after_ms:
        raise AssuranceError("CHECK_USE_EXPIRED")
    return permission


def _require_same_permission(
    authority: CurrentAuthority,
    identity: UseIdentity,
    ref: AssuranceRef,
    captured: CurrentReadPermission,
    now_ms: int,
) -> None:
    """Final barrier: the captured grant is still in force and CURRENT authority
    still grants the same access under the same policy.

    ``not_after_ms`` is a lease on the captured grant, not part of its identity: a
    production authority re-issues it relative to *now*, so comparing it would
    fail every re-check that is not in the same millisecond (Host real model run
    10, 2026-09-23). The captured lease is enforced; the fresh one is checked by
    ``_permission``.
    """
    if now_ms >= captured.not_after_ms:
        raise AssuranceError("CHECK_USE_EXPIRED")
    current = _permission(authority, identity, ref, now_ms)
    if (current.access, current.policy) != (captured.access, captured.policy):
        raise AssuranceError("RECHECK_REQUIRED")


@dataclass(frozen=True, slots=True)
class ResolvedRef:
    """计划 §3.1 的 ``ResolvedRef(body, pin, tenant, mission, issuer, state_witness)``。

    ``ref`` 是 kind + pin；``body_json`` 是按哈希核过的不可变正文（canonical）；``tenant_id`` /
    ``mission_id`` 来自真实查询（任务行），不是调用参数；``issuer`` 是写者身份：原 typed writer 的表、
    回执行上的 ``kind``、事件的 ``actor_type``；``state_witness_json`` 是行上除正文之外的列（可变生命
    周期），不参与正文哈希，使用点仍要按用途核它。``permission`` 只在读取器带现行授权时填，是解析时
    核过的访问见证；它的租期按"现在"重发，所以不进相等比较。
    """

    ref: AssuranceRef
    body_json: str
    tenant_id: str
    mission_id: str
    issuer: str
    state_witness_json: str
    permission: CurrentReadPermission | None = field(default=None, compare=False)

    @property
    def read_item(self) -> ReadItem:
        return ReadItem(
            "OBJECT",
            canonical(
                {
                    "kind": self.ref.kind,
                    "id": self.ref.pin.id,
                    "revision": self.ref.pin.revision,
                }
            ),
            self.ref.pin.content_hash,
        )


# Fixed SQL adapters, never table/column names supplied by a model. The final
# tuple element names an original integer revision; None means immutable @0.
_EXACT = {
    "requirements": ("requirements_revisions", "revision_id", "revision_json", "revision"),
    "task": ("task_semantics", "task_id", "binding_json", "binding_revision"),
    "observation": ("observations", "observation_id", "observation_json", None),
    "review": ("review_records", "record_id", "record_json", None),
    "review_package": ("review_packages", "package_id", "package_json", None),
    "acceptance": ("acceptances", "acceptance_id", "acceptance_json", None),
    "resolution": ("goal_resolutions", "resolution_id", "resolution_json", None),
    "result": ("results", "result_id", "json", None),
    "check_binding": ("assurance_check_bindings", "check_binding_id", "binding_json", None),
    "check_policy": ("assurance_criterion_policies", "policy_id", "policy_json", None),
    "method_instance": ("method_instances", "instance_id", "draft_json", "plan_revision"),
    "operation": ("operation_bindings", "operation_occurrence_id", "envelope_json", None),
    "method": ("method_contracts", "method_id", "contract_json", "method_version"),
    "input_manifest": ("input_manifests", "manifest_hash", "manifest_json", None),
    "commit_receipt": ("commit_receipts", "commit_id", "receipt_json", None),
}

# Fixed event bridges. Event.to_json (including the actual seq) is the original
# canonical body; payload-only hashes are not interchangeable with event refs.


# 完成规格 / 完成范围经原 OCC 读者读（第 2 批 A13）：合同解码、行身份与批准回执 / 任务绑定的
# 校验都是它的；这里只核归属、哈希并整形。
_COMPLETION = {
    "completion_spec": ("get_spec_by_id", "operation_completion_specs"),
    "completion_scope": ("get_scope_by_id", "operation_completion_scopes"),
}


class AssuranceReader:
    def __init__(
        self,
        store: Store,
        *,
        tenant_id: str,
        mission_id: str,
        authority: CurrentAuthority | None = None,
        identity: UseIdentity | None = None,
    ) -> None:
        self.store = store
        self.tenant_id = text(tenant_id)
        self.mission_id = text(mission_id)
        if (authority is None) != (identity is None):
            raise AssuranceError("CURRENT_READ_AUTHORITY_REQUIRED")
        if identity is not None and identity.mission_id != self.mission_id:
            raise AssuranceError("REF_SCOPE_MISMATCH", identity.mission_id)
        self.authority = authority
        self.identity = identity

    def _mission_locked(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT tenant_id FROM missions WHERE mission_id=?", (self.mission_id,)
        ).fetchone()
        if row is None or row[0] != self.tenant_id:
            raise AssuranceError("REF_SCOPE_MISMATCH")

    def _resolved(
        self, ref: AssuranceRef, body_json: str, issuer: str, witness: dict[str, Any], now_ms: int | None
    ) -> ResolvedRef:
        permission = None
        if self.authority is not None and self.identity is not None:
            at = int(self.store.now * 1000) if now_ms is None else now_ms
            permission = _permission(self.authority, self.identity, ref, at)
        return ResolvedRef(
            ref, body_json, self.tenant_id, self.mission_id, issuer, canonical(witness), permission
        )

    def read_exact_metadata(
        self,
        ref: AssuranceRef,
        *,
        now_ms: int | None = None,
        receipt_kind: str | None = None,
        receipt_subject: str | None = None,
    ) -> ResolvedRef:
        """Exact body identity only: no latest lookup and no assertion of current usability.

        ``receipt_kind`` / ``receipt_subject`` 只对 ``commit_receipt`` 有意义：调用方说出期望的写者
        （回执种类）与写的对象，行上不符按 ``REF_ISSUER_MISMATCH`` / ``REF_SUBJECT_MISMATCH`` 拒绝。
        """
        if ref.kind in EVENT_REF_KINDS:
            return self._event_metadata(ref, now_ms)
        if ref.kind in {"artifact", "source"}:
            from .assurance_blobs import read_blob_metadata

            return read_blob_metadata(self, ref, now_ms=now_ms)
        if ref.kind in _COMPLETION:
            return self._completion_metadata(ref, now_ms)
        if ref.kind not in _EXACT:
            raise AssuranceError("REF_KIND_UNSUPPORTED", ref.kind)
        table, identity, body_column, revision = _EXACT[ref.kind]
        if revision is None and ref.pin.revision != 0:
            raise AssuranceError("REF_REVISION_MISMATCH", ref.kind)
        with self.store.read_view() as connection:
            self._mission_locked(connection)
            if ref.kind in {"method", "input_manifest", "commit_receipt"}:
                where = f"{identity}=?"
                params: tuple[Any, ...] = (ref.pin.id,)
            else:
                where = f"mission_id=? AND {identity}=?"
                params = (self.mission_id, ref.pin.id)
            query = f"SELECT * FROM {table} WHERE {where}"
            if revision is not None:
                query += f" AND {revision}=?"
                params += (ref.pin.revision,)
            rows = connection.execute(query + " LIMIT 2", params).fetchall()
            if not rows:
                # A08：身份还在、钉住的版本不是来源现在持有的 → 当前性错误，按资产用自己的名字报。
                if revision is not None and connection.execute(
                    f"SELECT 1 FROM {table} WHERE {where}", params[: len(params) - 1]
                ).fetchone():
                    raise AssuranceError("SOURCE_NOT_CURRENT", ref.pin.id)
                raise AssuranceError("SOURCE_UNAVAILABLE", ref.pin.id)
            if len(rows) != 1:
                raise AssuranceError("REF_LOCATOR_AMBIGUOUS", ref.pin.id)
            row = dict(rows[0])
            limit = MAX_RECORD_BYTES if ref.kind == "input_manifest" else MAX_BYTES
            body = decode(row[body_column], limit=limit)
            if not isinstance(body, dict):
                raise AssuranceError("REF_BODY_INVALID", ref.pin.id)
            if ref.kind == "input_manifest" and row["origin_mission_id"] != self.mission_id:
                if (
                    connection.execute(
                        "SELECT 1 FROM input_manifest_bindings WHERE mission_id=? AND manifest_hash=?",
                        (self.mission_id, ref.pin.id),
                    ).fetchone()
                    is None
                ):
                    raise AssuranceError("REF_SCOPE_MISMATCH", ref.pin.id)
            issuer = table
            if ref.kind == "commit_receipt":
                if body.get("mission_id") != self.mission_id:
                    raise AssuranceError("REF_SCOPE_MISMATCH", ref.pin.id)
                # A09：真实 Commit writer 写的行，kind 是写者身份、subject_id 是它写的对象。
                issuer = text(row["kind"])
                if receipt_kind is not None and row["kind"] != receipt_kind:
                    raise AssuranceError("REF_ISSUER_MISMATCH", ref.pin.id)
                if receipt_subject is not None and row["subject_id"] != receipt_subject:
                    raise AssuranceError("REF_SUBJECT_MISMATCH", ref.pin.id)
            if (
                ref.kind == "method"
                and row["registry_status"] == "TRIAL_ADMITTED"
                and row["trial_scope_mission"] != self.mission_id
            ):
                raise AssuranceError("REF_SCOPE_MISMATCH", ref.pin.id)
            if ref.kind == "result":
                body = body["envelope"]  # exclude the mutable verification wrapper
            if fingerprint(body, limit=limit) != ref.pin.content_hash:
                raise AssuranceError("REF_BODY_CONFLICT", ref.pin.id)
            # The state witness is the row's own columns without the body column: the
            # body is carried (and hash-checked) once, not again as an escaped string that
            # pushed a 230 KB reviewer manifest past the 256 KB limit (2026-09-25 desktop run).
            witness = {key: value for key, value in row.items() if key != body_column}
            return self._resolved(ref, canonical(body, limit=limit), issuer, witness, now_ms)

    def _completion_metadata(self, ref: AssuranceRef, now_ms: int | None) -> ResolvedRef:
        from .operation_completion_store import OperationCompletionStore

        if ref.pin.revision != 0:
            raise AssuranceError("REF_REVISION_MISMATCH", ref.kind)
        method, table = _COMPLETION[ref.kind]
        with self.store.read_view():
            self._mission_locked(self.store.connection)
            try:
                row = getattr(OperationCompletionStore(self.store), method)(self.mission_id, ref.pin.id)
            except StoreConflict as error:
                raise AssuranceError("REF_BODY_CONFLICT", ref.pin.id) from error
            if row is None:
                raise AssuranceError("SOURCE_UNAVAILABLE", ref.pin.id)
            body = decode(row.pop("document_json"))
            row.pop("document", None)
            if fingerprint(body) != ref.pin.content_hash:
                raise AssuranceError("REF_BODY_CONFLICT", ref.pin.id)
            return self._resolved(ref, canonical(body), table, row, now_ms)

    def _event_metadata(self, ref: AssuranceRef, now_ms: int | None) -> ResolvedRef:
        if ref.pin.revision != 0:
            raise AssuranceError("REF_REVISION_MISMATCH", ref.kind)
        with self.store.read_view() as connection:
            self._mission_locked(connection)
            row = connection.execute(
                "SELECT * FROM events WHERE event_id=?", (ref.pin.id,)
            ).fetchone()
            if row is None:
                raise AssuranceError("SOURCE_UNAVAILABLE", ref.pin.id)
            if row["mission_id"] != self.mission_id:
                raise AssuranceError("REF_SCOPE_MISMATCH", ref.pin.id)
            if row["type"] != EVENT_REF_KINDS[ref.kind] or row["actor_type"] != "system":
                raise AssuranceError("REF_ISSUER_MISMATCH", ref.pin.id)
            decode(row["payload_json"])
            events = self.store.list_events(self.mission_id, after_seq=row["seq"] - 1, limit=1)
            if len(events) != 1 or events[0].id != ref.pin.id:
                raise AssuranceError("REF_LOCATOR_AMBIGUOUS", ref.pin.id)
            body = events[0].to_json()
            if fingerprint(body) != ref.pin.content_hash:
                raise AssuranceError("REF_BODY_CONFLICT", ref.pin.id)
            # System attribution is necessary, not proof of the original
            # execution/Provider/ledger input chain. The use-specific importer
            # still verifies those exact bindings before accepting this bridge.
            return self._resolved(ref, canonical(body), text(row["actor_type"]), dict(row), now_ms)


@dataclass(frozen=True, slots=True)
class CompleteRead:
    query_key: str
    schema_hash: str
    epochs: EpochSnapshot
    rows: tuple[str, ...]
    set_hash: str

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def read_item(self) -> ReadItem:
        return ReadItem(
            "QUERY_SET",
            self.query_key,
            # 2026-10-01（第 4 项）：指纹不含时钟。含时钟时任务里任何一次写入都让每张证书的
            # 查询集"变了"，逐项重读（orchestrator/assurance_recheck）永远比不上；时钟另由
            # 证书自己的 epoch 字段记着。
            fingerprint(
                {
                    "schema_hash": self.schema_hash,
                    "count": self.count,
                    "set_hash": self.set_hash,
                    "sql_exhausted": True,
                }
            ),
        )


# Read both polarities and every candidate branch. Scope-dependent filtering is
# performed by the original evaluator after this complete mission superset.
# Registry queries are global because trial_scope_mission is not ownership.
_SPECIAL_QUERIES = {
    "events": f"SELECT * FROM events WHERE mission_id=? AND type IN ({SOURCE_EVENT_SQL}) ORDER BY seq",
    "input_manifests": "SELECT m.* FROM input_manifests m WHERE m.origin_mission_id=? OR EXISTS(SELECT 1 FROM input_manifest_bindings b WHERE b.manifest_hash=m.manifest_hash AND b.mission_id=?) ORDER BY m.manifest_hash",
    "verifications": "SELECT v.* FROM verifications v JOIN results r ON r.result_id=v.result_id WHERE r.mission_id=? ORDER BY v.verification_id",
}

# The reader and barriers share one reviewed, frozen source inventory. Do not
# add a formal check, authority binding, or branch to one side without the
# other. Queries use original primary keys for stable complete-set ordering.
_QUERIES = (
    {
        table: f"SELECT * FROM {table} WHERE mission_id=? ORDER BY "
        + ",".join(SOURCE_PRIMARY_KEYS[table])
        for table in MISSION_TABLES
    }
    | {
        table: f"SELECT * FROM {table} ORDER BY " + ",".join(SOURCE_PRIMARY_KEYS[table])
        for table in GLOBAL_TABLES
        if table != "input_manifests"
    }
    | _SPECIAL_QUERIES
)


def read_complete_evidence_snapshot(
    reader: AssuranceReader,
    *,
    scope_id: str,
    maximum_rows: int = 20_000,
    maximum_bytes: int = 32 * 1024 * 1024,
) -> tuple[CompleteRead, ...]:
    """Exhaust all fixed queries in one original Store snapshot, or return nothing.

    It proves completeness of these local query sets, not sufficiency for every
    use: execution, authority and CAS readers must contribute their own channels.
    No result is returned if a table is missing, a row cannot decode, or a budget
    is exceeded. Even an empty set carries the captured epochs and schema.
    """
    from ..assurance.codec import integer

    text(scope_id)
    integer(maximum_rows, minimum=1, maximum=20_000)
    integer(maximum_bytes, minimum=1, maximum=32 * 1024 * 1024)
    result = []
    count = size = 0
    with reader.store.read_view() as connection:
        reader._mission_locked(connection)
        epochs = read_epochs_locked(connection, reader.mission_id)
        schema_hash = fingerprint(
            [
                dict(row)
                for row in connection.execute(
                    "SELECT version,name,checksum FROM orch_schema_migrations ORDER BY version"
                )
            ]
        )
        for kind, query in _QUERIES.items():
            rows = []
            digest = hashlib.sha256()
            digest.update(b"[")
            cursor = connection.execute(query, (reader.mission_id,) * query.count("?"))
            for row in cursor:
                for column in row.keys():
                    if (column == "json" or column.endswith("_json")) and row[column] is not None:
                        # A TEXT column containing malformed/duplicate-key JSON
                        # is not a complete readable record. Keep exact original
                        # bytes in the set digest after validating its structure.
                        try:
                            decode(row[column], limit=MAX_RECORD_BYTES)
                        except AssuranceError as error:
                            raise AssuranceError("EVIDENCE_EVALUATION_INCOMPLETE", kind) from error
                try:  # a whole-request manifest row may exceed MAX_BYTES; the set budget still bounds it
                    encoded = canonical(dict(row), limit=MAX_RECORD_BYTES)
                except AssuranceError as error:
                    raise AssuranceError("EVIDENCE_EVALUATION_INCOMPLETE", kind) from error
                count += 1
                size += len(encoded.encode("utf-8"))
                if count > maximum_rows or size > maximum_bytes:
                    raise AssuranceError("EVIDENCE_EVALUATION_INCOMPLETE", kind)
                if rows:
                    digest.update(b",")
                digest.update(encoded.encode("utf-8"))
                rows.append(encoded)
            digest.update(b"]")
            result.append(
                CompleteRead(
                    canonical(
                        {
                            "reader_version": READER_VERSION,
                            "mission": reader.mission_id,
                            "scope": scope_id,
                            "query_kind": kind,
                            "proposition_keys": "ALL",
                            "polarity": "BOTH",
                            "selection": "MISSION_SUPERSET",
                        }
                    ),
                    schema_hash,
                    epochs,
                    tuple(rows),
                    digest.hexdigest(),
                )
            )
    return tuple(result)
