"""Thin DeskPet adapter over the installed Agent-Reach package.

Agent-Reach owns channel discovery, setup diagnostics and backend selection.
DeskPet only normalizes bounded read/search results for its tool and workflow
contracts; it does not duplicate Agent-Reach's channel registry.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import threading
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit


_MAX_TEXT_CHARS = 120_000
_UPSTREAM_LOCK = threading.RLock()
_MAX_CHANNELS = 24
_MAX_BACKENDS = 8
_AUTH_RE = re.compile(r"(?i)(authorization\s*[\"']?\s*[:=]\s*)Bearer\s+[A-Za-z0-9._~+/-]{8,}")
_SECRET_RE = re.compile(r"(?i)(authorization|cookie|token|api[_-]?key|password)\s*[\"']?\s*[:=]\s*[\"']?[^\s,;\"']+")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}")
_QUERY_SECRET_RE = re.compile(r"(?i)([?&](?:token|api[_-]?key|password|secret)=)[^&#\s]+")


def _safe_message(value: object, *, limit: int = 300) -> str:
    text = _AUTH_RE.sub(r"\1Bearer [redacted]", str(value or ""))
    text = _SECRET_RE.sub(r"\1=[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _QUERY_SECRET_RE.sub(r"\1[redacted]", text)
    return text[:limit]


def _public_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme not in {"http", "https"} or not host or parsed.username:
            return False
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class AgentReachEvidence:
    ok: bool
    channel: str
    active_backend: str | None
    status: str
    text: str = ""
    title: str = ""
    url: str = ""
    reason_code: str | None = None
    message: str = ""

    def observation(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "active_backend": self.active_backend,
            "status": self.status,
            "reason_code": self.reason_code,
            "message": self.message,
        }


class AgentReachPort:
    """Bounded Agent-Reach diagnostics and channel execution."""

    def doctor(self, *, names: Iterable[str] | None = None) -> dict[str, dict[str, object]]:
        try:
            from agent_reach.channels import get_all_channels
            from agent_reach.config import Config
        except Exception:
            return {
                "agent_reach": {
                    "status": "off",
                    "name": "Agent-Reach",
                    "message": "Agent-Reach package is not installed.",
                    "tier": 0,
                    "backends": [],
                    "active_backend": None,
                }
            }

        wanted = (
            {str(value)[:50] for value in list(names)[:_MAX_CHANNELS]}
            if names is not None
            else None
        )
        results: dict[str, dict[str, object]] = {}
        with _UPSTREAM_LOCK:
            for channel in list(get_all_channels())[:_MAX_CHANNELS]:
                try:
                    name = _safe_message(getattr(channel, "name", ""), limit=50)
                    if not name or (wanted is not None and name not in wanted):
                        continue
                    status, message = channel.check(Config())
                    active = getattr(channel, "active_backend", None)
                    results[name] = {
                        "status": _safe_message(status, limit=16),
                        "name": _safe_message(getattr(channel, "description", name), limit=100),
                        "message": _safe_message(message),
                        "tier": max(0, min(int(getattr(channel, "tier", 0)), 9)),
                        "backends": [
                            _safe_message(value, limit=80)
                            for value in list(getattr(channel, "backends", []))[:_MAX_BACKENDS]
                        ],
                        "active_backend": _safe_message(active, limit=80) if active is not None else None,
                    }
                except Exception:  # noqa: BLE001 - one channel cannot break doctor
                    fallback = _safe_message(getattr(channel, "name", "unknown"), limit=50)
                    results[fallback or "unknown"] = {
                        "status": "error",
                        "name": fallback or "unknown",
                        "message": "Channel health check failed.",
                        "tier": 0,
                        "backends": [],
                        "active_backend": None,
                    }
        return results

    @staticmethod
    def _channels():
        from agent_reach.channels import get_all_channels

        return get_all_channels()

    def _match_url(self, url: str):
        with _UPSTREAM_LOCK:
            channels = self._channels()
            for channel in channels:
                if channel.name == "web":
                    continue
                try:
                    if channel.can_handle(url):
                        return channel
                except Exception:  # noqa: BLE001
                    continue
            return next((channel for channel in channels if channel.name == "web"), None)

    def channel_name_for_url(self, url: str) -> str:
        channel = self._match_url(url)
        return str(getattr(channel, "name", "web") or "web")

    def read_url(self, url: str, *, max_chars: int = _MAX_TEXT_CHARS) -> AgentReachEvidence:
        target = (url or "").strip()
        if not _public_http_url(target):
            return AgentReachEvidence(
                False, "web", None, "error", reason_code="invalid_url",
                message="URL must be an absolute public http(s) URL.",
            )
        try:
            from agent_reach.channels import get_channel
        except Exception:
            return AgentReachEvidence(
                False, "web", None, "off", reason_code="not_installed",
                message="Agent-Reach package is not installed.",
            )

        with _UPSTREAM_LOCK:
            channel = self._match_url(target)
            requested_name = str(getattr(channel, "name", "web") or "web")
            health = self.doctor(names=[requested_name, "web"])
            executor = channel if callable(getattr(channel, "read", None)) else None
            active_backend = (health.get(requested_name) or {}).get("active_backend")
            if executor is None:
                executor = get_channel("web")
                active_backend = (health.get("web") or {}).get("active_backend")
            if executor is None or not callable(getattr(executor, "read", None)):
                return AgentReachEvidence(
                    False, requested_name, None, "off",
                    reason_code="no_read_backend",
                    message="No Agent-Reach read backend is available.",
                )
            try:
                text = str(executor.read(target) or "").strip()
            except Exception as exc:  # noqa: BLE001
                return AgentReachEvidence(
                    False, requested_name, str(active_backend) if active_backend else None,
                    "degraded", reason_code="read_failed",
                    message=_safe_message(type(exc).__name__),
                )
            resolved_backend = (
                str(active_backend)
                if active_backend
                else str(getattr(executor, "active_backend", "")) or None
            )
        if not text:
            return AgentReachEvidence(
                False, requested_name, str(active_backend) if active_backend else None,
                "empty", reason_code="empty",
                message="Agent-Reach backend returned no readable text.",
            )
        bounded = text[: max(1, min(int(max_chars), _MAX_TEXT_CHARS))]
        title_match = re.search(r"(?m)^Title:\s*(.+)$", bounded)
        title = title_match.group(1).strip() if title_match else target
        return AgentReachEvidence(
            True,
            requested_name,
            resolved_backend,
            "ok",
            text=bounded,
            title=title[:200],
            url=target,
            message="Agent-Reach channel read completed.",
        )

    async def read_url_async(self, url: str, *, max_chars: int = _MAX_TEXT_CHARS) -> AgentReachEvidence:
        return await asyncio.to_thread(self.read_url, url, max_chars=max_chars)


agent_reach_port = AgentReachPort()


__all__ = ["AgentReachEvidence", "AgentReachPort", "agent_reach_port"]
