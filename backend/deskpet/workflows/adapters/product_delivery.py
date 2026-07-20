# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Product delivery bridge for durable workflow intents.

``ProductDeliveryAdapter.handlers()`` is the wiring surface consumed by
``build_workflow_service(delivery_handlers=...)``.  Artifact intents reuse the
existing ``ToolArtifact`` envelope and persist it through ``SessionDB``;
receipt intents reuse signed Receipt v2 JSONL persistence.  Both projections
use the workflow event id as their idempotency identity.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...memory.session_db import SessionDB
from ...tools.artifact import (
    ToolArtifact,
    extract_artifacts_from_result,
    sha256_file_async,
)
from ...tools.receipt import ToolReceipt, make_receipt
from ...tools.receipt_store import ReceiptStore
from ..contracts import canonical_json
from ..store.run_store import WorkflowRunStore


DeliveryHandler = Callable[
    [Mapping[str, Any], Mapping[str, Any]], Awaitable[dict[str, Any]]
]
ArtifactPublisher = Callable[[dict[str, Any]], Awaitable[None]]
ContentResolver = Callable[[str], Awaitable[bytes | str]]

_ARTIFACT_CHANNELS = frozenset({"artifact", "artifact_message"})
_RECEIPT_CHANNELS = frozenset({"receipt", "receipt_jsonl"})
_SUCCESS_OUTCOMES = frozenset({"completed", "ok", "success", "succeeded"})
_FAILED_OUTCOMES = frozenset({"blocked", "error", "failed", "failure"})
_CANCELLED_OUTCOMES = frozenset({"cancelled", "canceled"})
_PENDING_OUTCOMES = frozenset({"accepted", "pending", "running"})


@dataclass(frozen=True)
class WorkflowMessageProjection:
    role: str
    projection_kind: str
    context_visibility: str
    skip_embed: bool


_WORKFLOW_MESSAGE_PROJECTIONS = {
    "workflow.final_assistant": WorkflowMessageProjection(
        "assistant", "final_assistant", "conversation", False
    ),
    "workflow.progress": WorkflowMessageProjection(
        "assistant", "workflow_progress", "exclude", True
    ),
    "workflow.accepted": WorkflowMessageProjection(
        "assistant", "workflow_accepted", "exclude", True
    ),
    "workflow.decision": WorkflowMessageProjection(
        "assistant", "workflow_decision", "exclude", True
    ),
    "workflow.final": WorkflowMessageProjection(
        "assistant", "workflow_final_status", "exclude", True
    ),
    "workflow.artifact_card": WorkflowMessageProjection(
        "assistant", "artifact_card", "exclude", True
    ),
}


def workflow_message_projection(event_type: str) -> WorkflowMessageProjection:
    """Return the only valid SessionDB classification for a workflow event."""
    normalized = str(event_type or "").strip().lower()
    try:
        return _WORKFLOW_MESSAGE_PROJECTIONS[normalized]
    except KeyError as exc:
        raise ProductDeliveryError(
            f"unsupported workflow message event type: {normalized!r}"
        ) from exc


class ProductDeliveryError(RuntimeError):
    """A malformed intent or failed durable projection."""


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ProductDeliveryError(f"workflow delivery requires {field_name}")
    return text


def _content_ref(payload: Mapping[str, Any]) -> str | None:
    value = str(payload.get("content_ref") or "").strip()
    if not value:
        return None
    if not value.startswith("sha256:"):
        raise ProductDeliveryError("workflow content_ref must use sha256:<digest>")
    digest = value[7:]
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ProductDeliveryError("workflow content_ref has an invalid SHA-256 digest")
    return value


async def resolve_workflow_content_text(
    payload: Mapping[str, Any], resolver: ContentResolver | None
) -> str | None:
    """Resolve a terminal content pointer without mutating durable payloads."""

    ref = _content_ref(payload)
    if ref is None:
        return None
    if resolver is None:
        raise ProductDeliveryError("workflow content_ref has no configured resolver")
    value = await resolver(ref)
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProductDeliveryError("workflow content_ref is not strict UTF-8") from exc
    elif isinstance(value, str):
        text = value
    else:
        raise ProductDeliveryError("workflow content resolver returned an unsupported value")
    if not text.strip():
        raise ProductDeliveryError("workflow content_ref resolved to empty text")
    return text


def _event_id(event: Mapping[str, Any], delivery: Mapping[str, Any]) -> str:
    event_value = _required_text(event.get("event_id"), "event_id")
    delivery_value = str(delivery.get("event_id") or "").strip()
    if delivery_value and delivery_value != event_value:
        raise ProductDeliveryError("delivery event_id does not match its event")
    return event_value


def _intent(event: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    raw_payload = event.get("payload")
    if not isinstance(raw_payload, Mapping):
        raise ProductDeliveryError("workflow event payload must be an object")
    payload = dict(raw_payload)

    nested_intent = payload.get("intent")
    if isinstance(nested_intent, Mapping):
        payload = dict(nested_intent)

    kind = str(payload.get("kind") or "").strip().lower()
    nested_payload = payload.get("payload")
    if kind and isinstance(nested_payload, Mapping):
        return kind, dict(nested_payload)
    return kind, payload


def _tool_name(
    event: Mapping[str, Any], payload: Mapping[str, Any], fallback: str
) -> str:
    return str(
        payload.get("tool")
        or payload.get("tool_name")
        or event.get("workflow_name")
        or fallback
    ).strip()


def _report_preview(value: object) -> str:
    current = value
    for _ in range(3):
        if not isinstance(current, Mapping):
            break
        for key in ("report_md", "summary", "text"):
            text = current.get(key)
            if isinstance(text, str) and text.strip():
                return text
        nested = current.get("report")
        if nested is current:
            break
        current = nested
    if isinstance(value, (Mapping, list)):
        return canonical_json(value)
    return str(value or "")


def _artifact_list(
    event: Mapping[str, Any], payload: Mapping[str, Any]
) -> list[ToolArtifact]:
    tool_name = _tool_name(event, payload, "artifact_create")
    explicit = payload.get("artifacts")
    if isinstance(explicit, list) and explicit:
        encoded = json.dumps({"ok": True, "artifacts": explicit}, ensure_ascii=False)
        artifacts = extract_artifacts_from_result(tool_name=tool_name, result_json=encoded)
        if artifacts:
            return artifacts

    nested = payload.get("artifact")
    if isinstance(nested, Mapping):
        encoded = json.dumps(
            {"ok": True, "artifacts": [dict(nested)]}, ensure_ascii=False
        )
        artifacts = extract_artifacts_from_result(
            tool_name=tool_name, result_json=encoded
        )
        if artifacts:
            return artifacts

    path = payload.get("path")
    url = payload.get("url")
    if isinstance(path, str) and path.strip():
        item = {
            "kind": payload.get("artifact_kind", "file"),
            "path": path,
            "mime": payload.get("mime"),
            "title": payload.get("title", ""),
            "preview": payload.get("preview"),
            "size_bytes": payload.get("size_bytes"),
            "sha256": payload.get("sha256"),
        }
        encoded = json.dumps({"ok": True, "artifacts": [item]}, ensure_ascii=False)
        return extract_artifacts_from_result(tool_name=tool_name, result_json=encoded)
    if isinstance(url, str) and url.strip():
        return [
            ToolArtifact(
                kind="url",
                url=url,
                title=str(payload.get("title") or ""),
                preview=str(payload.get("preview") or "") or None,
            )
        ]

    report = payload.get("report")
    if report is not None:
        preview = str(payload.get("preview") or "").strip() or _report_preview(report)
        return [
            ToolArtifact(
                kind="text",
                title=str(
                    payload.get("title")
                    or payload.get("artifact_type")
                    or "Research report"
                ),
                preview=preview,
                sha256=(
                    str(payload.get("sha256"))
                    if isinstance(payload.get("sha256"), str)
                    and len(str(payload.get("sha256"))) == 64
                    else hashlib.sha256(
                        canonical_json(report).encode("utf-8")
                    ).hexdigest()
                ),
            )
        ]
    raise ProductDeliveryError("artifact intent does not contain a valid artifact")


async def _materialize_file_evidence(artifacts: list[ToolArtifact]) -> None:
    for artifact in artifacts:
        if artifact.kind not in {"file", "image"} or not artifact.path:
            continue
        path = Path(artifact.path)
        if not path.is_file():
            raise ProductDeliveryError(f"artifact file does not exist: {path}")
        digest = await sha256_file_async(path)
        if digest is None:
            raise ProductDeliveryError(f"artifact sha256 is unavailable: {path}")
        if artifact.sha256 and artifact.sha256.lower() != digest:
            raise ProductDeliveryError(f"artifact sha256 mismatch: {path}")
        artifact.sha256 = digest
        try:
            artifact.size_bytes = path.stat().st_size
        except OSError as exc:
            raise ProductDeliveryError(f"artifact stat failed: {path}") from exc


def _artifact_shas(payload: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("sha256", "output_hash"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    for key in ("artifact_shas", "shas"):
        raw = payload.get(key)
        if isinstance(raw, list):
            values.extend(str(item) for item in raw if str(item))
    raw_artifacts = payload.get("artifacts")
    if isinstance(raw_artifacts, list):
        for item in raw_artifacts:
            if isinstance(item, Mapping) and isinstance(item.get("sha256"), str):
                values.append(str(item["sha256"]))
    return list(dict.fromkeys(values))


def _outcome(payload: Mapping[str, Any]) -> str:
    return str(payload.get("outcome") or payload.get("status") or "").strip().lower()


def _event_time(event: Mapping[str, Any]) -> datetime:
    raw = event.get("created_at")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    return datetime.now(timezone.utc)


def stable_workflow_receipt_id(event_id: str, phase: str) -> str:
    """Normative UUID5 identity for a Receipt v2 event projection."""

    normalized_phase = str(phase).strip().lower()
    if normalized_phase not in {"accepted", "delivered"}:
        raise ValueError(f"unsupported workflow receipt phase: {phase}")
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"deskpet://workflow-event/{event_id}/receipt/{normalized_phase}",
        )
    )


class ProductDeliveryAdapter:
    """Persist Graph product intents using the existing DeskPet stores.

    Wiring::

        adapter = ProductDeliveryAdapter(session_db=session_db, receipt_store=receipt_store)
        service = await build_workflow_service(root, delivery_handlers=adapter.handlers())

    ``load_verify_evidence`` is the Graph-specific ledger view to pass to an
    existing ``VerifyGate``.  Accepted, failed, and cancelled records remain in
    the audit ledger but are never returned as completion evidence.
    """

    def __init__(
        self,
        *,
        session_db: SessionDB,
        receipt_store: ReceiptStore,
        workflow_store: WorkflowRunStore | None = None,
        artifact_publisher: ArtifactPublisher | None = None,
        content_resolver: ContentResolver | None = None,
    ) -> None:
        self.session_db = session_db
        self.receipt_store = receipt_store
        self.workflow_store = workflow_store
        self.artifact_publisher = artifact_publisher
        self.content_resolver = content_resolver

    def handlers(self) -> dict[str, DeliveryHandler]:
        return {
            "artifact": self._deliver_artifact_handler,
            "artifact_message": self._deliver_artifact_handler,
            "receipt": self._deliver_receipt_handler,
            "receipt_jsonl": self._deliver_receipt_handler,
        }

    async def _deliver_artifact_handler(
        self, event: Mapping[str, Any], delivery: Mapping[str, Any]
    ) -> object:
        result = await self.deliver_artifact(event, delivery)
        if delivery.get("manifest_ref") is None:
            return result
        from ..delivery import DeliveryAttemptResultV1

        if result.get("discarded") is True:
            return DeliveryAttemptResultV1.discarded_fenced("session_epoch_mismatch")
        return DeliveryAttemptResultV1.delivered(
            "artifact_projection_persisted",
            artifact_projection=result.get("artifact_projection"),
        )

    async def _deliver_receipt_handler(
        self, event: Mapping[str, Any], delivery: Mapping[str, Any]
    ) -> object:
        result = await self.deliver_receipt(event, delivery)
        if delivery.get("manifest_ref") is None:
            return result
        from ..delivery import DeliveryAttemptResultV1

        return DeliveryAttemptResultV1.delivered("receipt_persisted")

    async def dispatch(
        self, event: Mapping[str, Any], delivery: Mapping[str, Any]
    ) -> dict[str, Any]:
        channel = _required_text(delivery.get("channel"), "delivery channel").lower()
        if channel in _ARTIFACT_CHANNELS:
            return await self.deliver_artifact(event, delivery)
        if channel in _RECEIPT_CHANNELS:
            return await self.deliver_receipt(event, delivery)
        raise ProductDeliveryError(f"unsupported product delivery channel: {channel}")

    async def deliver_artifact(
        self, event: Mapping[str, Any], delivery: Mapping[str, Any]
    ) -> dict[str, Any]:
        event_id = _event_id(event, delivery)
        target_id = _required_text(delivery.get("target_id"), "delivery target_id")
        kind, payload = _intent(event)
        if kind and kind not in {"artifact", "artifact_card"}:
            raise ProductDeliveryError(f"artifact channel received {kind!r} intent")
        status = _outcome(payload)
        if status in _FAILED_OUTCOMES | _CANCELLED_OUTCOMES:
            raise ProductDeliveryError("failed workflows cannot publish completion artifacts")

        resolved_text = await resolve_workflow_content_text(payload, self.content_resolver)
        if resolved_text is not None:
            # v6 report artifacts carry only a content-addressed Markdown
            # pointer in the durable event.  Convert it to the existing text
            # artifact envelope at the physical-delivery boundary.
            payload = {
                **payload,
                "artifact_type": str(payload.get("artifact_type") or "research_report"),
                "report": resolved_text,
                "preview": str(payload.get("preview") or resolved_text),
                "text": str(payload.get("text") or resolved_text),
                "sha256": str(payload.get("content_ref"))[7:],
            }
        artifacts = _artifact_list(event, payload)
        await _materialize_file_evidence(artifacts)
        tool_name = _tool_name(event, payload, "artifact_create")
        text = str(payload.get("text") or payload.get("summary") or "")
        envelope = {
            "tool": tool_name,
            "ok": True,
            "result": text,
            "text": text,
            "artifacts": [item.to_dict() for item in artifacts],
            "session_id": target_id,
            "workflow": {
                "event_id": event_id,
                "run_id": str(event.get("run_id") or payload.get("run_id") or ""),
            },
        }
        content = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        projection = workflow_message_projection("workflow.artifact_card")
        if self.workflow_store is not None:
            run_id = str(event.get("run_id") or payload.get("run_id") or "")
            ref = await self.workflow_store.get_session_ref(run_id, "delivery")
            if ref is None or str(ref["session_id"]) != target_id:
                return {"channel": str(delivery.get("channel")), "event_id": event_id, "discarded": True}
            message_id = await self.session_db.append_message_if_epoch(
                target_id,
                projection.role,
                content,
                expected_epoch=int(ref["session_epoch"]),
                workflow_event_id=event_id,
                tool_call_id="",
                projection_kind=projection.projection_kind,
                context_visibility=projection.context_visibility,
                skip_embed=projection.skip_embed,
            )
            if message_id is None:
                return {"channel": str(delivery.get("channel")), "event_id": event_id, "discarded": True}
        else:
            message_id = await self.session_db.append_message(
                session_id=target_id,
                role=projection.role,
                content=content,
                tool_call_id="",
                workflow_event_id=event_id,
                projection_kind=projection.projection_kind,
                context_visibility=projection.context_visibility,
                skip_embed=projection.skip_embed,
            )
        if self.artifact_publisher is not None:
            await self.artifact_publisher(
                {
                    **envelope,
                    "message_id": message_id,
                    "workflow_event_id": event_id,
                }
            )
        return {
            "channel": str(delivery.get("channel")),
            "event_id": event_id,
            "message_id": message_id,
            "artifact_count": len(artifacts),
        }

    async def deliver_receipt(
        self, event: Mapping[str, Any], delivery: Mapping[str, Any]
    ) -> dict[str, Any]:
        event_id = _event_id(event, delivery)
        target_id = _required_text(delivery.get("target_id"), "delivery target_id")
        kind, payload = _intent(event)
        if kind in {"assistant", "final_assistant"}:
            # Compatibility drain for v2 runs created before assistant output
            # moved from business channel ``final`` to ``final_assistant``.
            # Text was already persisted/broadcast by the other projections;
            # consume the accidental receipt without minting completion proof.
            return {
                "channel": str(delivery.get("channel")),
                "event_id": event_id,
                "legacy_assistant_ignored": True,
                "completion_evidence": False,
            }
        if kind and kind not in {"accepted", "receipt", "final"}:
            raise ProductDeliveryError(f"receipt channel received {kind!r} intent")

        outcome = _outcome(payload)
        phase = str(payload.get("phase") or "").strip().lower()
        event_type = str(event.get("event_type") or "").strip().lower()
        accepted = (
            phase == "accepted"
            or kind == "accepted"
            or event_type.endswith(".accepted")
        )
        if accepted:
            if outcome and outcome not in _PENDING_OUTCOMES:
                raise ProductDeliveryError("accepted receipt must have a pending outcome")
            phase = "accepted"
            receipt_outcome = "pending"
            ok = False
            error_class = None
        else:
            phase = "delivered"
            if outcome in _SUCCESS_OUTCOMES:
                receipt_outcome = "success"
                ok = True
                error_class = None
            elif outcome in _FAILED_OUTCOMES:
                receipt_outcome = "failed"
                ok = False
                error_class = "workflow_failed"
            elif outcome in _CANCELLED_OUTCOMES:
                receipt_outcome = "failed"
                ok = False
                error_class = "workflow_cancelled"
            else:
                raise ProductDeliveryError(
                    f"receipt intent must be terminal; got outcome={outcome or '<missing>'!r}"
                )

        now = _event_time(event)
        tool_name = _tool_name(event, payload, "workflow")
        run_id = str(payload.get("run_id") or event.get("run_id") or "").strip() or None
        receipt_id = stable_workflow_receipt_id(event_id, phase)
        receipt = make_receipt(
            tool_name=tool_name,
            args={"event_id": event_id, "intent": payload},
            started_at=now,
            ended_at=now,
            ok=ok,
            session_id=target_id,
            error_class=error_class,
            artifact_shas=_artifact_shas(payload),
            receipt_id=receipt_id,
            phase=phase,
            outcome=receipt_outcome,
            run_id=run_id,
            node_execution_id=str(payload.get("node_execution_id") or "") or None,
            effect_id=event_id,
            secret=self.receipt_store.key,
        )
        self.receipt_store.append_once(receipt)
        persisted = next(
            (
                item
                for item in self.receipt_store.load_session(target_id)
                if item.receipt_id == receipt_id
            ),
            None,
        )
        if persisted is None:
            raise ProductDeliveryError(f"receipt projection was not persisted: {receipt_id}")
        return {
            "channel": str(delivery.get("channel")),
            "event_id": event_id,
            "receipt_id": persisted.receipt_id,
            "phase": persisted.phase,
            "outcome": persisted.outcome,
            "completion_evidence": self.is_verify_evidence(persisted),
        }

    @staticmethod
    def is_verify_evidence(receipt: ToolReceipt) -> bool:
        return (
            receipt.sig_version == 2
            and receipt.phase == "delivered"
            and receipt.outcome == "success"
            and receipt.ok
        )

    def load_verify_evidence(self, session_id: str) -> list[ToolReceipt]:
        return [
            item
            for item in self.receipt_store.load_session(session_id)
            if self.is_verify_evidence(item)
        ]


def build_product_delivery_handlers(
    *, session_db: SessionDB, receipt_store: ReceiptStore
) -> dict[str, DeliveryHandler]:
    """Small main-wiring factory when the adapter object is not otherwise needed."""

    return ProductDeliveryAdapter(
        session_db=session_db,
        receipt_store=receipt_store,
    ).handlers()


__all__ = [
    "ContentResolver",
    "DeliveryHandler",
    "ProductDeliveryAdapter",
    "ProductDeliveryError",
    "build_product_delivery_handlers",
    "resolve_workflow_content_text",
    "stable_workflow_receipt_id",
]
