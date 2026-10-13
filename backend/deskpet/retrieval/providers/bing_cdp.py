from __future__ import annotations

from urllib.parse import quote_plus

import httpx

from deskpet.tools import research_cdp_edge, search_provider
from deskpet.tools.research_cdp_edge import cdp_edge_available

from ..contracts import PublicErrorCode, SearchBudget, SearchRequest
from .base import ProviderFailure, candidates_from_rows


class BingCDPProvider:
    name = "bing-cdp"
    capabilities = frozenset({"cdp", "cn", "global"})

    async def is_available(self) -> bool:

        return cdp_edge_available()

    async def search(self, request: SearchRequest, budget: SearchBudget, client: httpx.AsyncClient):

        if not await budget.claim_cdp():
            raise ProviderFailure(PublicErrorCode.BUDGET_EXHAUSTED)
        url = f"https://www.bing.com/search?q={quote_plus(request.query)}"
        html = await research_cdp_edge.cdp_edge_render(
            url, timeout=min(budget.per_provider_timeout_s, budget.remaining_s)
        )
        if not html:
            raise ProviderFailure(PublicErrorCode.TIMEOUT)
        try:
            rows = search_provider.parse_bing_html(html, max_results=request.max_results)
            captcha = search_provider._looks_like_bing_captcha(html, rows)
        except Exception as exc:
            raise ProviderFailure(PublicErrorCode.INVALID_RESPONSE) from exc
        if captcha:
            raise ProviderFailure(PublicErrorCode.CAPTCHA)
        return candidates_from_rows(self.name, rows)
