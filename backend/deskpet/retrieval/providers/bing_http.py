from __future__ import annotations
import httpx
from .base import ProviderFailure, candidates_from_rows
from ..contracts import PublicErrorCode, SearchBudget, SearchRequest

class BingHTTPProvider:
    name = "bing"
    capabilities = frozenset({"html", "cn", "global"})
    async def is_available(self) -> bool: return True
    async def search(self, request: SearchRequest, budget: SearchBudget, client: httpx.AsyncClient):
        from deskpet.tools.search_provider import parse_bing_html
        try:
            response = await client.get("https://www.bing.com/search", params={"q": request.query, "mkt": "zh-CN" if request.region == "cn-zh" else "en-US"})
            if response.status_code in {403, 429}: raise ProviderFailure(PublicErrorCode.BLOCKED, response.status_code)
            response.raise_for_status()
            return candidates_from_rows(self.name, parse_bing_html(response.text, max_results=request.max_results))
        except ProviderFailure: raise
        except httpx.HTTPError as exc: raise ProviderFailure(PublicErrorCode.HTTP_ERROR) from exc
