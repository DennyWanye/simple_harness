from __future__ import annotations
import httpx
from .base import ProviderFailure, candidates_from_rows, raise_for_search_status
from ..contracts import PublicErrorCode, SearchBudget, SearchRequest

class SearXNGProvider:
    name = "searxng"
    capabilities = frozenset({"json", "cn", "global"})
    def __init__(self, url: str) -> None: self.url = url
    async def is_available(self) -> bool: return bool(self.url)
    async def search(self, request: SearchRequest, budget: SearchBudget, client: httpx.AsyncClient):
        from deskpet.tools.search_provider import _parse_searxng_json
        try:
            response = await client.get(self.url, params={"q": request.query, "format": "json"})
            raise_for_search_status(response)
            if "json" not in response.headers.get("content-type", "").lower(): raise ProviderFailure(PublicErrorCode.INVALID_RESPONSE)
            return candidates_from_rows(self.name, _parse_searxng_json(response.json(), request.max_results))
        except ProviderFailure: raise
        except httpx.HTTPError as exc: raise ProviderFailure(PublicErrorCode.HTTP_ERROR) from exc
        except (ValueError, TypeError) as exc: raise ProviderFailure(PublicErrorCode.INVALID_RESPONSE) from exc
