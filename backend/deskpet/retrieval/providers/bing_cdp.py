from __future__ import annotations
from urllib.parse import quote_plus
import httpx
from .base import ProviderFailure, candidates_from_rows
from ..contracts import PublicErrorCode, SearchBudget, SearchRequest

class BingCDPProvider:
    name = "bing-cdp"
    capabilities = frozenset({"cdp", "cn", "global"})
    async def is_available(self) -> bool:
        from deskpet.tools.research_cdp_edge import cdp_edge_available
        return cdp_edge_available()
    async def search(self, request: SearchRequest, budget: SearchBudget, client: httpx.AsyncClient):
        from deskpet.tools.research_cdp_edge import cdp_edge_render
        from deskpet.tools.search_provider import _looks_like_bing_captcha, parse_bing_html
        if not await budget.claim_cdp(): raise ProviderFailure(PublicErrorCode.BUDGET_EXHAUSTED)
        url = f"https://www.bing.com/search?q={quote_plus(request.query)}"
        html = await cdp_edge_render(url, timeout=min(budget.per_provider_timeout_s, budget.remaining_s))
        if not html: raise ProviderFailure(PublicErrorCode.TIMEOUT)
        rows = parse_bing_html(html, max_results=request.max_results)
        if _looks_like_bing_captcha(html, rows): raise ProviderFailure(PublicErrorCode.CAPTCHA)
        return candidates_from_rows(self.name, rows)
