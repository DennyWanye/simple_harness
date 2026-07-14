from __future__ import annotations

import dataclasses
import json

import pytest

from deskpet.retrieval.contracts import (
    AttemptStatus, ProviderAttempt, RetrievalCandidate, SearchRequest, SearchResponse,
)


def test_contracts_are_frozen_slotted_and_json_safe():
    request = SearchRequest(" query ", max_results=999, hydrate_top=99)
    assert request.query == "query"
    assert request.max_results == 50
    assert request.hydrate_top == 3
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.query = "changed"  # type: ignore[misc]
    candidate = RetrievalCandidate("id", "https://a.test", "https://a.test", "A")
    response = SearchResponse("query", (candidate,), (ProviderAttempt("p", AttemptStatus.HIT, 1, 1),))
    assert json.loads(json.dumps(response.to_dict()))["attempts"][0]["status"] == "hit"


def test_request_rejects_unknown_mode():
    with pytest.raises(ValueError, match="mode"):
        SearchRequest("q", mode="slow")  # type: ignore[arg-type]
