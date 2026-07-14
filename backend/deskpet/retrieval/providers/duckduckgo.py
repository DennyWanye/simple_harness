from __future__ import annotations
import httpx
from .base import ProviderFailure, candidates_from_rows
from ..contracts import PublicErrorCode, SearchBudget, SearchRequest

class DuckDuckGoProvider:
    name = "duckduckgo"
    capabilities = frozenset({"html", "cn", "global"})
    async def is_available(self) -> bool: return True
    async def search(self, request: SearchRequest, budget: SearchBudget, client: httpx.AsyncClient):
        from deskpet.tools.search_provider import parse_ddg_html
        try:
            response = await client.post("https://html.duckduckgo.com/html/", data={"q": request.query, "kl": request.region or "us-en"})
            if response.status_code in {403, 429}: raise ProviderFailure(PublicErrorCode.BLOCKED, response.status_code)
            response.raise_for_status()
            return candidates_from_rows(self.name, parse_ddg_html(response.text, max_results=request.max_results))
        except ProviderFailure: raise
        except httpx.HTTPError as exc: raise ProviderFailure(PublicErrorCode.HTTP_ERROR) from exc
