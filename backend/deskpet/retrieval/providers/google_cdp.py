from __future__ import annotations
from urllib.parse import quote_plus
import httpx
from .base import ProviderFailure, candidates_from_rows
from ..contracts import PublicErrorCode, SearchBudget, SearchRequest

class GoogleCDPProvider:
    name = "google-cdp"
    capabilities = frozenset({"cdp", "global"})
    async def is_available(self) -> bool:
        from deskpet.tools.research_cdp_edge import cdp_edge_available
        return cdp_edge_available()
    async def probe(self, client: httpx.AsyncClient) -> bool:
        try:
            response = await client.get("https://www.google.com/generate_204", timeout=0.5)
            return response.status_code in {200, 204}
        except Exception:
            return False
    async def search(self, request: SearchRequest, budget: SearchBudget, client: httpx.AsyncClient):
        from deskpet.tools.research_cdp_edge import cdp_edge_render
        from deskpet.tools.search_provider import _looks_like_google_captcha, _parse_google_html
        if not await budget.claim_cdp(): raise ProviderFailure(PublicErrorCode.BUDGET_EXHAUSTED)
        url = f"https://www.google.com/search?q={quote_plus(request.query)}"
        html = await cdp_edge_render(url, timeout=min(budget.per_provider_timeout_s, budget.remaining_s))
        if not html: raise ProviderFailure(PublicErrorCode.TIMEOUT)
        rows = _parse_google_html(html, request.max_results)
        if _looks_like_google_captcha(html, rows): raise ProviderFailure(PublicErrorCode.CAPTCHA)
        return candidates_from_rows(self.name, rows)
