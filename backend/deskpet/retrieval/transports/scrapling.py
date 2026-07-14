"""Raw optional Scrapling transport; no extraction or fallback policy."""

from __future__ import annotations

from typing import Any


class ScraplingTransport:
    name = "scrapling"

    def fetch(self, url: str, *, timeout: float) -> dict[str, Any]:
        try:
            from scrapling.fetchers import Fetcher  # type: ignore

            page = Fetcher.get(url, stealthy_headers=True, timeout=timeout)
            body = getattr(page, "body", b"")
            if isinstance(body, bytes):
                html = body.decode(getattr(page, "encoding", None) or "utf-8", "ignore")
            else:
                html = str(body or getattr(page, "html_content", "") or "")
            return {
                "ok": True,
                "status": int(getattr(page, "status", 0) or 0),
                "html": html,
                "url_final": str(getattr(page, "url", url) or url),
                "content_type": "text/html; charset=utf-8",
                "fetcher": self.name,
            }
        except ModuleNotFoundError:
            return {"ok": False, "error": "scrapling_unavailable", "retriable": True}
        except Exception:
            return {"ok": False, "error": "scrapling_failed", "retriable": True}
