"""Bounded official-source retrieval port for the DeepResearch v6 graph.

The semantic compiler owns the question decomposition.  This adapter only
consumes frozen ``ResearchSpecV1.work_dimensions`` and turns each dimension
into a bounded official-site search.  It deliberately never reads
``normalized_question`` and contains no answer values, years, or answer URLs.

The public ``load_pages`` method returns ref-only graph inputs.  Page bodies and
source locators (the only objects containing raw URLs) are first persisted in a
``RegisteredBlobStore``.  Only pages whose *final* fetched URL still belongs to
the resolved authority are returned.
"""

from __future__ import annotations

from ..definitions.deep_research_v6_evidence_contracts import V6FetchedPageRefPayloadV1
from .deep_research_v6_evidence_domains.core import (
    MAX_FETCH_CONCURRENCY,
    MAX_FETCH_COUNT,
    PAGE_DEADLINE_MS,
    PARENT_DEADLINE_MS,
)
from .deep_research_v6_evidence_domains.fetching import FetchMixin
from .deep_research_v6_evidence_domains.retrieval import RetrievalMixin
from .deep_research_v6_evidence_domains.support import (
    MAX_QUERY_COUNT,
    DeepResearchV6RetrievalResult,
)


class DeepResearchV6EvidenceRuntime(RetrievalMixin, FetchMixin):
    """Real SearchGateway/fetch bridge with one parent and bounded page leases."""


__all__ = [
    "DeepResearchV6EvidenceRuntime",
    "DeepResearchV6RetrievalResult",
    "V6FetchedPageRefPayloadV1",
    "MAX_FETCH_CONCURRENCY",
    "MAX_FETCH_COUNT",
    "MAX_QUERY_COUNT",
    "PAGE_DEADLINE_MS",
    "PARENT_DEADLINE_MS",
]
