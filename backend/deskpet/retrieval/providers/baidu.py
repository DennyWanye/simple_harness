from __future__ import annotations
import httpx
from .base import ProviderFailure, candidates_from_rows, raise_for_search_status
from ..contracts import PublicErrorCode, SearchBudget, SearchRequest

class BaiduProvider:
    name = "baidu"
    capabilities = frozenset({"html", "cn"})
    async def is_available(self) -> bool: return True
    async def search(self, request: SearchRequest, budget: SearchBudget, client: httpx.AsyncClient):
        from deskpet.tools.search_provider import parse_baidu_html
        try:
            response = await client.get("https://www.baidu.com/s", params={"wd": request.query})
            raise_for_search_status(response)
            try:
                rows = parse_baidu_html(response.text, max_results=request.max_results)
            except Exception as exc:
                raise ProviderFailure(PublicErrorCode.INVALID_RESPONSE) from exc
            return candidates_from_rows(self.name, rows)
        except ProviderFailure: raise
        except httpx.HTTPError as exc: raise ProviderFailure(PublicErrorCode.HTTP_ERROR) from exc
