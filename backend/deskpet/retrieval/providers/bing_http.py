from __future__ import annotations

import httpx

from deskpet.tools import search_provider

from ..contracts import PublicErrorCode, SearchBudget, SearchRequest
from .base import ProviderFailure, candidates_from_rows, raise_for_search_status


class BingHTTPProvider:
    name = "bing"
    capabilities = frozenset({"html", "cn", "global"})

    async def is_available(self) -> bool:
        return True

    async def search(self, request: SearchRequest, budget: SearchBudget, client: httpx.AsyncClient):

        try:
            response = await client.get(
                "https://www.bing.com/search",
                params={
                    "q": request.query,
                    "mkt": "zh-CN" if request.region == "cn-zh" else "en-US",
                },
            )
            raise_for_search_status(response)
            try:
                rows = search_provider.parse_bing_html(
                    response.text, max_results=request.max_results
                )
            except Exception as exc:
                raise ProviderFailure(PublicErrorCode.INVALID_RESPONSE) from exc
            return candidates_from_rows(self.name, rows)
        except ProviderFailure:
            raise
        except httpx.HTTPError as exc:
            raise ProviderFailure(PublicErrorCode.HTTP_ERROR) from exc
