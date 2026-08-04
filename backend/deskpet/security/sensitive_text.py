"""Shared sensitive-text redaction with no product or storage dependency."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _Pattern:
    kind: str
    regex: re.Pattern[str]


_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern("ANTHROPIC_KEY", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    _Pattern("API_KEY", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{20,}\b")),
    _Pattern("GITHUB_TOKEN", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
    _Pattern(
        "JWT",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
        ),
    ),
    _Pattern("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    _Pattern("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    _Pattern("PHONE_CN", re.compile(r"\b1[3-9]\d{9}\b")),
    _Pattern(
        "EMAIL",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    _Pattern(
        "CREDENTIAL",
        re.compile(
            r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S+",
        ),
    ),
)


def redact_sensitive_text(text: str) -> str:
    """Return text with sensitive spans replaced by stable typed markers."""

    if not text:
        return text
    redacted = text
    for pattern in _PATTERNS:
        redacted = pattern.regex.sub(
            f"[REDACTED:{pattern.kind}]",
            redacted,
        )
    return redacted


__all__ = ["redact_sensitive_text"]
