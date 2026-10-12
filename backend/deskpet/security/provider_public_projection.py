"""Default-deny public projection for physical and auxiliary Provider calls."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from deskpet.security.redaction import TraceRedactor
from deskpet.security.sensitive_text import redact_sensitive_text

_REDACTOR = TraceRedactor()


class ProviderPublicProjectorV1:
    """Expose only low-sensitivity, structured Provider metadata."""

    _USAGE_FIELDS = frozenset(
        {"input_tokens", "output_tokens", "total_tokens", "cached_tokens"}
    )

    def project(
        self,
        record: Mapping[str, Any],
        *,
        outcome: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw_usage = outcome.get("usage", {}) if isinstance(outcome, Mapping) else {}
        usage = {
            key: int(value)
            for key, value in raw_usage.items()
            if key in self._USAGE_FIELDS
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        }
        projected = {
            "provider_id": self._safe_string(record.get("provider_id")),
            "model_id": self._safe_string(record.get("model_id") or record.get("model")),
            "purpose": self._safe_string(record.get("purpose")),
            "provider_stage": self._safe_string(
                record.get("provider_stage") or record.get("workload_class")
            ),
            "status": self._safe_string(record.get("status")) or "unknown",
            "duration_ms": self._safe_number(record.get("duration_ms")),
            "usage": usage,
            "narration_code": self._safe_string(record.get("narration_code")),
            "assistant_content_ref": self._safe_string(
                record.get("assistant_content_ref")
            ),
            "invocation_id": self._safe_string(record.get("invocation_id")),
            "detached": bool(record.get("detached", False)),
        }
        return _REDACTOR.redact(projected)

    @staticmethod
    def _safe_string(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        return redact_sensitive_text(value[:512])

    @staticmethod
    def _safe_number(value: Any) -> int | float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return value
        return None


__all__ = ["ProviderPublicProjectorV1"]
