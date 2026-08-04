"""Owner-fenced, version-stable Companion notification detail pagination."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable, Mapping, Sequence

from deskpet.capabilities.contracts import OwnerScopeKey
from deskpet.security.redaction import TraceRedactor

from .store import canonical_hash, canonical_json

_REQUEST_FIELDS = frozenset(
    {
        "notification_id",
        "section",
        "cursor",
        "page_size",
        "expected_detail_version",
    }
)
_SECTIONS = frozenset(
    {
        "overview",
        "evidence",
        "diff",
        "evaluation",
        "decision",
        "operation_receipt",
        "current_binding",
        "audit",
    }
)
_REDACTOR = TraceRedactor()

_AUTHORITY_TABLES: Mapping[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "growth_events": (
        "evidence",
        (
            "event_id",
            "event_hash",
            "source_kind",
            "source_ref",
            "context_key",
            "root_run_id",
            "retry_of",
            "payload_json",
            "content_state",
            "reason_code",
            "created_at",
            "updated_at",
        ),
        ("event_id", "source_ref", "root_run_id", "retry_of"),
    ),
    "candidate_evidence": (
        "evidence",
        ("candidate_id", "event_id", "reason_code", "created_at"),
        ("candidate_id", "event_id"),
    ),
    "candidate_artifacts": (
        "diff",
        (
            "candidate_id",
            "package_id",
            "proposal_source_kind",
            "proposal_source_ref",
            "proposal_source_hash",
            "reflection_job_id",
            "candidate_mode",
            "target_id",
            "source_owner_key",
            "source_scope",
            "source_scope_key",
            "source_version",
            "source_manifest_hash",
            "source_binding_generation",
            "target_owner_key",
            "target_scope",
            "target_scope_key",
            "target_expected_absent",
            "target_expected_binding_generation",
            "evidence_set_hash",
            "attempt_generation",
            "status",
            "reason_code",
            "created_at",
            "updated_at",
        ),
        (
            "candidate_id",
            "package_id",
            "proposal_source_ref",
            "reflection_job_id",
        ),
    ),
    "candidate_packages": (
        "diff",
        (
            "package_id",
            "candidate_mode",
            "pack_id",
            "version",
            "candidate_content_hash",
            "candidate_manifest_hash",
            "candidate_package_hash",
            "archive_hash",
            "source_facts_json",
            "target_facts_json",
            "effect_topology_hash",
            "content_state",
            "blob_cleanup_state",
            "reason_code",
            "created_at",
            "updated_at",
        ),
        ("package_id",),
    ),
    "candidate_package_files": (
        "diff",
        (
            "package_id",
            "relative_path",
            "file_kind",
            "file_mode",
            "content_hash",
            "size_bytes",
            "reason_code",
            "created_at",
        ),
        ("package_id",),
    ),
    "candidate_package_sources": (
        "diff",
        (
            "package_id",
            "candidate_id",
            "build_id",
            "evidence_set_json",
            "evidence_set_hash",
            "builder_launch_id",
            "builder_child_run_id",
            "builder_receipt_ref",
            "builder_receipt_hash",
            "provenance_state",
            "reason_code",
            "created_at",
            "updated_at",
        ),
        (
            "package_id",
            "candidate_id",
            "build_id",
            "builder_launch_id",
            "builder_child_run_id",
            "builder_receipt_ref",
        ),
    ),
    "candidate_builds": (
        "diff",
        (
            "build_id",
            "source_kind",
            "source_ref",
            "reflection_job_id",
            "proposal_ref",
            "proposal_hash",
            "evidence_set_json",
            "evidence_set_hash",
            "candidate_mode",
            "source_fence_json",
            "source_fence_hash",
            "target_fence_json",
            "target_fence_hash",
            "builder_launch_id",
            "builder_child_run_id",
            "draft_receipt_ref",
            "draft_receipt_hash",
            "candidate_ref",
            "candidate_hash",
            "status",
            "reason_code",
            "created_at",
            "updated_at",
        ),
        (
            "build_id",
            "source_ref",
            "reflection_job_id",
            "proposal_ref",
            "builder_launch_id",
            "builder_child_run_id",
            "draft_receipt_ref",
            "candidate_ref",
        ),
    ),
    "evaluation_runs": (
        "evaluation",
        (
            "evaluation_id",
            "candidate_id",
            "candidate_mode",
            "suite_hash",
            "attempt_key",
            "baseline_kind",
            "old_snapshot_hash",
            "candidate_snapshot_hash",
            "source_owner_key",
            "source_scope",
            "source_scope_key",
            "source_pack_id",
            "source_version",
            "source_manifest_hash",
            "source_binding_generation",
            "absent_baseline_ref",
            "absent_baseline_hash",
            "runner_id",
            "runner_policy_hash",
            "provider_id",
            "model_id",
            "status",
            "reason_code",
            "created_at",
            "updated_at",
        ),
        ("evaluation_id", "candidate_id", "attempt_key", "absent_baseline_ref"),
    ),
    "evaluation_reports": (
        "evaluation",
        (
            "report_id",
            "evaluation_id",
            "candidate_package_hash",
            "dataset_hash",
            "suite_hash",
            "results_root_hash",
            "verdict",
            "reason_code",
            "required_case_count",
            "committed_result_count",
            "created_at",
        ),
        ("report_id", "evaluation_id"),
    ),
    "risk_assessments": (
        "evaluation",
        (
            "risk_id",
            "candidate_id",
            "candidate_package_hash",
            "static_preflight_json",
            "effect_topology_diff_json",
            "risk",
            "risk_hash",
            "reason_code",
            "created_at",
        ),
        ("risk_id", "candidate_id"),
    ),
    "growth_decisions": (
        "decision",
        (
            "decision_id",
            "candidate_id",
            "report_id",
            "risk_id",
            "candidate_mode",
            "source_fence_json",
            "target_owner_key",
            "target_scope",
            "target_scope_key",
            "target_expected_absent",
            "target_expected_binding_generation",
            "decision",
            "actor",
            "reason_code",
            "activation_package_hash",
            "activation_code_digest",
            "activation_risk_ack",
            "decision_hash",
            "created_at",
        ),
        ("decision_id", "candidate_id", "report_id", "risk_id"),
    ),
    "capability_activation_requests": (
        "decision",
        (
            "activation_request_id",
            "request_fingerprint",
            "action",
            "candidate_id",
            "candidate_mode",
            "rollback_kind",
            "activation_mode",
            "target_owner_key",
            "target_scope",
            "target_scope_key",
            "pack_id",
            "target_version",
            "target_manifest_hash",
            "candidate_package_hash",
            "archive_hash",
            "code_digest",
            "source_fence_json",
            "target_expected_absent",
            "target_expected_binding_generation",
            "report_id",
            "risk_id",
            "decision_id",
            "risk_ack",
            "cause_ref",
            "manager_operation_id",
            "runtime_set_ref",
            "runtime_set_hash",
            "status",
            "reason_code",
            "created_at",
            "updated_at",
        ),
        (
            "activation_request_id",
            "candidate_id",
            "report_id",
            "risk_id",
            "decision_id",
            "cause_ref",
            "manager_operation_id",
        ),
    ),
    "capability_activation_receipts": (
        "operation_receipt",
        (
            "activation_request_id",
            "manager_operation_id",
            "action",
            "candidate_mode",
            "pack_id",
            "version",
            "manifest_hash",
            "source_fence_hash",
            "runtime_set_ref",
            "runtime_set_hash",
            "target_owner_key",
            "target_scope",
            "binding_generation",
            "owner_binding_set_stamp",
            "process_projection_fingerprint",
            "target_user_owner_binding_set_stamp",
            "fallback_owner_key",
            "fallback_scope",
            "fallback_scope_key",
            "fallback_binding_id",
            "fallback_binding_generation",
            "fallback_pack_id",
            "fallback_version",
            "fallback_manifest_hash",
            "fallback_owner_binding_set_stamp",
            "fallback_process_projection_fingerprint",
            "fallback_runtime_set_ref",
            "fallback_runtime_set_hash",
            "absence_proof_hash",
            "result_hash",
            "settled_at",
            "reason_code",
        ),
        ("activation_request_id", "manager_operation_id"),
    ),
    "capability_version_supports": (
        "operation_receipt",
        (
            "pack_id",
            "version",
            "manifest_hash",
            "candidate_id",
            "evidence_set_json",
            "evidence_set_hash",
            "build_receipt_ref",
            "build_receipt_hash",
            "report_id",
            "decision_id",
            "activation_receipt_hash",
            "support_state",
            "support_hash",
            "reason_code",
            "created_at",
            "updated_at",
        ),
        (
            "candidate_id",
            "build_receipt_ref",
            "report_id",
            "decision_id",
            "activation_receipt_hash",
        ),
    ),
    "audit_events": (
        "audit",
        (
            "audit_id",
            "actor",
            "action",
            "reason_code",
            "before_hash",
            "after_hash",
            "lineage_ref",
            "audit_hash",
            "created_at",
        ),
        ("audit_id", "lineage_ref"),
    ),
    "lineage_edges": (
        "audit",
        (
            "from_kind",
            "from_id",
            "to_kind",
            "to_id",
            "relation",
            "reason_code",
            "created_at",
        ),
        ("from_id", "to_id"),
    ),
}


class CompanionDetailQueryError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message
        super().__init__(message or code)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _redact(value: Any) -> Any:
    return _REDACTOR.redact(_jsonable(value))


def _bounded_item(value: Any) -> Any:
    safe = _redact(_jsonable(value))
    encoded = canonical_json(safe).encode("utf-8")
    if len(encoded) <= 8192:
        return safe
    digest = hashlib.sha256(encoded).hexdigest()
    preview = encoded[:4096].decode("utf-8", errors="ignore")
    bounded = {
        "truncated": True,
        "sha256": digest,
        "preview": preview,
        "original_bytes": len(encoded),
    }
    while len(canonical_json(bounded).encode("utf-8")) > 8192 and preview:
        preview = preview[: max(0, len(preview) * 3 // 4)]
        bounded["preview"] = preview
    return bounded


def _decode_authority_row(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = {"source_table": table}
    redacted = str(row.get("content_state") or "") == "redacted"
    forgotten = str(row.get("provenance_state") or "") == "forgotten"
    for key in row.keys():
        item = row[key]
        if key.endswith("_json"):
            public_key = key[:-5]
            if redacted or forgotten:
                value[public_key] = {
                    "state": "forgotten" if forgotten else "redacted"
                }
                continue
            try:
                item = json.loads(str(item)) if item is not None else None
            except (TypeError, ValueError):
                item = {"state": "unavailable"}
            value[public_key] = item
        else:
            value[str(key)] = item
    return value


def _identity_values(row: Mapping[str, Any], fields: Iterable[str]) -> set[str]:
    values: set[str] = set()
    for field in fields:
        value = row.get(field)
        if value not in (None, ""):
            values.add(str(value))
    return values


class CompanionDetailQueryPort:
    """The only read path for evidence/diff/audit detail shown by the UI."""

    def __init__(
        self,
        *,
        store: Any,
        platform_store: Any | None,
        cursor_secret: bytes,
        cursor_ttl_seconds: int = 600,
    ) -> None:
        if len(cursor_secret) < 32:
            raise ValueError("cursor_secret must contain at least 32 bytes")
        self.store = store
        self.platform_store = platform_store
        self.cursor_secret = bytes(cursor_secret)
        self.cursor_ttl_seconds = int(cursor_ttl_seconds)

    async def query(
        self,
        *,
        frozen_identity: Any,
        control_epoch: int,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        if set(request) != _REQUEST_FIELDS:
            raise CompanionDetailQueryError(
                "unavailable", "detail request fields do not match the protocol"
            )
        notification_id = str(request["notification_id"] or "").strip()
        section = str(request["section"] or "").strip()
        try:
            page_size = int(request["page_size"])
        except (TypeError, ValueError) as exc:
            raise CompanionDetailQueryError("unavailable", "invalid page size") from exc
        if (
            not notification_id
            or section not in _SECTIONS
            or page_size < 1
            or page_size > 20
        ):
            raise CompanionDetailQueryError("unavailable")

        owner = frozen_identity.owner
        try:
            vc0 = dict(self.store.get_detail_version(owner))
            notification = self.store.get_notification(owner, notification_id)
        except Exception as exc:
            raise CompanionDetailQueryError("unavailable") from exc
        if notification is None or str(notification.get("status")) in {
            "redacted",
            "superseded",
        }:
            raise CompanionDetailQueryError("unavailable")

        discovery = self._authoritative_detail(owner, notification)
        platform_keys = self._platform_keys(
            owner_key=str(frozen_identity.owner_key),
            authority=discovery,
            notification=notification,
        )
        snapshot0 = await self._platform_snapshot(platform_keys)
        authority = self._authoritative_detail(owner, notification)
        if authority["lineage_hash"] != discovery["lineage_hash"]:
            raise CompanionDetailQueryError("detail_changed")
        if self._platform_keys(
            owner_key=str(frozen_identity.owner_key),
            authority=authority,
            notification=notification,
        ) != platform_keys:
            raise CompanionDetailQueryError("detail_changed")
        detail_version = self._detail_version(
            frozen_identity=frozen_identity,
            notification=notification,
            vc=vc0,
            platform_vector=self._platform_vector(snapshot0),
        )
        expected = request.get("expected_detail_version")
        if expected not in (None, "") and str(expected) != detail_version:
            raise CompanionDetailQueryError("detail_changed")

        last_sort_key = ""
        cursor = request.get("cursor")
        if cursor not in (None, ""):
            cursor_body = self._decode_cursor(str(cursor))
            if (
                cursor_body.get("profile_id") != owner.profile_id
                or int(cursor_body.get("profile_generation") or 0)
                != owner.profile_generation
                or cursor_body.get("notification_id") != notification_id
                or cursor_body.get("section") != section
                or cursor_body.get("detail_version") != detail_version
                or int(cursor_body.get("control_epoch") or 0) != int(control_epoch)
                or float(cursor_body.get("expires_at") or 0) < time.time()
            ):
                raise CompanionDetailQueryError("cursor_invalid")
            last_sort_key = str(cursor_body.get("last_sort_key") or "")
            if not last_sort_key:
                raise CompanionDetailQueryError("cursor_invalid")

        raw_items = self._section_items(
            section=section,
            authority=authority,
            platform_snapshot=snapshot0,
        )
        remaining = [item for item in raw_items if item[0] > last_sort_key]
        selected = remaining[:page_size]
        page = [
            self._normalize_item(section, sort_key, item)
            for sort_key, item in selected
        ]

        vc1 = dict(self.store.get_detail_version(owner))
        vector1 = await self._platform_tokens(platform_keys)
        if canonical_hash(vc0) != canonical_hash(vc1) or self._platform_vector_hash(
            self._platform_vector(snapshot0)
        ) != self._platform_vector_hash(vector1):
            raise CompanionDetailQueryError("detail_changed")
        stable_version = self._detail_version(
            frozen_identity=frozen_identity,
            notification=notification,
            vc=vc1,
            platform_vector=vector1,
        )
        if stable_version != detail_version:
            raise CompanionDetailQueryError("detail_changed")

        next_cursor = (
            self._encode_cursor(
                {
                    "profile_id": owner.profile_id,
                    "profile_generation": owner.profile_generation,
                    "notification_id": notification_id,
                    "section": section,
                    "detail_version": stable_version,
                    "last_sort_key": selected[-1][0],
                    "control_epoch": int(control_epoch),
                    "expires_at": time.time() + self.cursor_ttl_seconds,
                }
            )
            if selected and len(remaining) > len(selected)
            else None
        )
        payload = {
            "notification_id": notification_id,
            "section": section,
            "detail_version": stable_version,
            "items": page,
            "next_cursor": next_cursor,
        }
        if len(canonical_json(payload).encode("utf-8")) > 65536:
            raise CompanionDetailQueryError(
                "unavailable", "detail response exceeds 64 KiB"
            )
        return payload

    async def current_detail_version(
        self,
        *,
        frozen_identity: Any,
        notification: Mapping[str, Any],
    ) -> str:
        """Compute the exact stable Vc + VpVector version used in envelopes."""

        owner = frozen_identity.owner
        try:
            vc0 = dict(self.store.get_detail_version(owner))
            discovery = self._authoritative_detail(owner, notification)
            keys = self._platform_keys(
                owner_key=str(frozen_identity.owner_key),
                authority=discovery,
                notification=notification,
            )
            snapshot0 = await self._platform_snapshot(keys)
            vc1 = dict(self.store.get_detail_version(owner))
            vector1 = await self._platform_tokens(keys)
        except CompanionDetailQueryError:
            raise
        except Exception as exc:
            raise CompanionDetailQueryError("unavailable") from exc
        if canonical_hash(vc0) != canonical_hash(vc1) or self._platform_vector_hash(
            self._platform_vector(snapshot0)
        ) != self._platform_vector_hash(vector1):
            raise CompanionDetailQueryError("detail_changed")
        return self._detail_version(
            frozen_identity=frozen_identity,
            notification=notification,
            vc=vc1,
            platform_vector=vector1,
        )

    def _authoritative_detail(
        self,
        owner: Any,
        notification: Mapping[str, Any],
    ) -> dict[str, Any]:
        rows_by_table: dict[str, list[dict[str, Any]]] = {}
        try:
            with self.store.read() as db:
                for table, (_section, columns, _links) in _AUTHORITY_TABLES.items():
                    selected = ",".join(columns)
                    rows = db.execute(
                        f"""SELECT {selected} FROM {table}
                            WHERE profile_id=? AND profile_generation=?""",
                        (owner.profile_id, owner.profile_generation),
                    ).fetchall()
                    rows_by_table[table] = [dict(row) for row in rows]
        except Exception as exc:
            raise CompanionDetailQueryError("unavailable") from exc

        known = {
            str(value)
            for value in notification.get("source_refs") or ()
            if value not in (None, "")
        }
        selected_rows: dict[str, list[dict[str, Any]]] = {
            table: [] for table in _AUTHORITY_TABLES
        }
        selected_hashes: set[str] = set()
        for _ in range(len(_AUTHORITY_TABLES) + 2):
            changed = False
            for table, (_section, _columns, links) in _AUTHORITY_TABLES.items():
                for row in rows_by_table[table]:
                    row_key = canonical_hash([table, row])
                    if row_key in selected_hashes:
                        continue
                    values = _identity_values(row, links)
                    if not values.intersection(known):
                        continue
                    selected_hashes.add(row_key)
                    selected_rows[table].append(row)
                    before = len(known)
                    known.update(values)
                    changed = changed or len(known) != before
            if not changed:
                break

        sections: dict[str, list[tuple[str, Mapping[str, Any]]]] = {
            name: [] for name in _SECTIONS
        }
        overview = {
            "source_table": "notifications",
            "notification_id": str(notification["notification_id"]),
            "kind": str(notification["kind"]),
            "summary": notification["summary"],
            "status": str(notification["status"]),
            "reason_code": str(notification["reason_code"]),
            "source_refs": sorted(
                str(value) for value in notification.get("source_refs") or ()
            ),
            "payload_hash": str(notification["payload_hash"]),
            "redaction_version": int(notification.get("redaction_version") or 0),
        }
        sections["overview"].append(
            (
                f"overview|notification|{notification['notification_id']}",
                overview,
            )
        )
        decoded_rows: list[dict[str, Any]] = []
        for table, rows in selected_rows.items():
            section, _columns, links = _AUTHORITY_TABLES[table]
            for row in rows:
                decoded = _decode_authority_row(table, row)
                decoded_rows.append(decoded)
                identity = sorted(_identity_values(row, links))
                sort_key = (
                    f"{section}|{table}|"
                    f"{canonical_hash([identity, row])}"
                )
                sections[section].append((sort_key, decoded))
        for section in sections:
            sections[section].sort(key=lambda item: item[0])
        return {
            "sections": sections,
            "rows": decoded_rows,
            "lineage_hash": canonical_hash(
                {
                    table: sorted(rows, key=canonical_hash)
                    for table, rows in selected_rows.items()
                }
            ),
        }

    @staticmethod
    def _platform_keys(
        *,
        owner_key: str,
        authority: Mapping[str, Any],
        notification: Mapping[str, Any],
    ) -> tuple[OwnerScopeKey, ...]:
        raw_keys: list[Any] = []
        for row in authority.get("rows") or ():
            if not isinstance(row, Mapping):
                continue
            for prefix in ("target", "source", "fallback"):
                scope = row.get(f"{prefix}_scope")
                scope_key = row.get(f"{prefix}_scope_key")
                if scope not in (None, "") and scope_key not in (None, ""):
                    raw_keys.append(
                        {
                            "owner_key": row.get(f"{prefix}_owner_key"),
                            "scope": scope,
                            "scope_key": scope_key,
                        }
                    )
            for value in row.values():
                if (
                    isinstance(value, Mapping)
                    and value.get("scope") not in (None, "")
                    and value.get("scope_key") not in (None, "")
                ):
                    raw_keys.append(value)

        detail = notification.get("detail")
        if isinstance(detail, Mapping):
            for field in ("platform_keys", "precedence_keys", "fallback_keys"):
                value = detail.get(field)
                if isinstance(value, Sequence) and not isinstance(
                    value, (str, bytes)
                ):
                    raw_keys.extend(value)
        keys: list[OwnerScopeKey] = []
        for raw in raw_keys:
            if not isinstance(raw, Mapping):
                continue
            scope = str(raw.get("scope") or "")
            expected_owner = "builtin" if scope == "builtin" else owner_key
            supplied_owner = str(raw.get("owner_key") or expected_owner)
            if supplied_owner != expected_owner:
                raise CompanionDetailQueryError("unavailable")
            try:
                keys.append(
                    OwnerScopeKey(
                        supplied_owner,
                        scope,
                        str(raw["scope_key"]),
                    )
                )
            except Exception as exc:
                raise CompanionDetailQueryError("unavailable") from exc
        return tuple(sorted(set(keys)))

    async def _platform_snapshot(self, keys: Sequence[OwnerScopeKey]) -> Any:
        if not keys:
            return {"tokens": [], "bindings": []}
        if self.platform_store is None:
            raise CompanionDetailQueryError("unavailable")
        try:
            return await self.platform_store.read_detail_snapshot(keys)
        except Exception as exc:
            raise CompanionDetailQueryError(
                "unavailable", "catalog_reconciling"
            ) from exc

    async def _platform_tokens(self, keys: Sequence[OwnerScopeKey]) -> Any:
        if not keys:
            return []
        if self.platform_store is None:
            raise CompanionDetailQueryError("unavailable")
        reader = getattr(self.platform_store, "read_detail_token_vector", None)
        try:
            if reader is not None:
                return await reader(keys)
            return self._platform_vector(
                await self.platform_store.read_detail_snapshot(keys)
            )
        except Exception as exc:
            raise CompanionDetailQueryError(
                "unavailable", "catalog_reconciling"
            ) from exc

    @staticmethod
    def _platform_vector(snapshot: Any) -> Any:
        if isinstance(snapshot, Mapping):
            return snapshot.get("tokens") or []
        return getattr(snapshot, "tokens", ())

    @staticmethod
    def _platform_vector_hash(vector: Any) -> str:
        if isinstance(vector, Mapping):
            return canonical_hash(vector)
        if isinstance(vector, Sequence):
            return canonical_hash([_jsonable(item) for item in vector])
        return canonical_hash(
            {
                "fingerprint": getattr(vector, "fingerprint", canonical_hash([])),
                "items": [
                    item.to_dict() for item in getattr(vector, "items", ())
                ],
            }
        )

    def _detail_version(
        self,
        *,
        frozen_identity: Any,
        notification: Mapping[str, Any],
        vc: Mapping[str, Any],
        platform_vector: Any,
    ) -> str:
        return canonical_hash(
            {
                "profile_id": frozen_identity.owner.profile_id,
                "profile_generation": frozen_identity.owner.profile_generation,
                "notification_payload_hash": notification["payload_hash"],
                "companion_version": dict(vc),
                "platform_vector": self._platform_vector_hash(platform_vector),
            }
        )

    @staticmethod
    def _section_items(
        *,
        section: str,
        authority: Mapping[str, Any],
        platform_snapshot: Any,
    ) -> list[tuple[str, Any]]:
        if section == "current_binding":
            if isinstance(platform_snapshot, Mapping):
                bindings = list(platform_snapshot.get("bindings") or [])
            else:
                bindings = [
                    _jsonable(item)
                    for item in getattr(platform_snapshot, "bindings", ())
                ]
            return sorted(
                (
                    (
                        "current_binding|"
                        + canonical_hash(_jsonable(binding)),
                        _jsonable(binding),
                    )
                    for binding in bindings
                ),
                key=lambda item: item[0],
            )
        sections = authority.get("sections")
        if not isinstance(sections, Mapping):
            return []
        return list(sections.get(section) or [])

    @staticmethod
    def _normalize_item(
        section: str, sort_key: str, value: Any
    ) -> Mapping[str, Any]:
        bounded = _bounded_item(value)
        if isinstance(bounded, Mapping):
            item = dict(bounded)
        else:
            item = {"summary": str(bounded)}
        item.setdefault(
            "id",
            canonical_hash([section, sort_key])[:24],
        )
        return item

    def _encode_cursor(self, body: Mapping[str, Any]) -> str:
        payload = canonical_json(dict(body)).encode("utf-8")
        signature = hmac.new(self.cursor_secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")

    def _decode_cursor(self, token: str) -> Mapping[str, Any]:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            canonical_token = base64.urlsafe_b64encode(raw).decode(
                "ascii"
            ).rstrip("=")
            if not hmac.compare_digest(canonical_token, token):
                raise ValueError("non-canonical cursor")
            payload, supplied = raw[:-32], raw[-32:]
            expected = hmac.new(
                self.cursor_secret, payload, hashlib.sha256
            ).digest()
            if len(raw) <= 32 or not hmac.compare_digest(supplied, expected):
                raise ValueError("invalid signature")
            body = json.loads(payload.decode("utf-8"))
            if not isinstance(body, Mapping):
                raise ValueError("invalid payload")
            return body
        except Exception as exc:
            raise CompanionDetailQueryError("cursor_invalid") from exc


__all__ = ["CompanionDetailQueryError", "CompanionDetailQueryPort"]
