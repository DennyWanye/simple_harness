"""Shared asynchronous retrieval services for DeskPet."""

from .contracts import (
    EvidenceDocument,
    FetchRequest,
    ProviderAttempt,
    RetrievalCandidate,
    SearchBudget,
    SearchRequest,
    SearchResponse,
)
from .runtime import get_default_gateway

__all__ = [
    "EvidenceDocument",
    "FetchRequest",
    "ProviderAttempt",
    "RetrievalCandidate",
    "SearchBudget",
    "SearchRequest",
    "SearchResponse",
    "get_default_gateway",
]
