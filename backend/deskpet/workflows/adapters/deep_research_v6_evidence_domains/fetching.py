"""Seed discovery, page fetches, durable page results and canonical page refs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit

from ....retrieval.contracts import EvidenceDocument, FetchRequest
from ....retrieval.official_sources import OfficialSourceResolver, OfficialSourceTargetV1
from ...contracts import JsonValue, NodeExecutionIdentity, canonical_json
from ...deadlines import (
    DeadlineValidationError,
    DurableDeadlineV1,
    create_child_deadline,
    remaining_timeout_seconds,
    resume_deadline,
)
from ...definitions.deep_research_v6_contracts import format_blob_ref
from ...definitions.deep_research_v6_evidence_contracts import V6FetchedPageRefPayloadV1
from ...definitions.deep_research_v6_retrieval_contracts import (
    PageExtractionResultV1,
    SourceLocatorV1,
)
from ...store import BlobRef
from ...trace.models import SpanStatus
from .core import EvidenceRuntimeCore
from .support import (
    _SPACE,
    _CandidatePlan,
    _FetchOutcome,
    _SeedDiscoveryOutcome,
    _stable_id,
    _VerifiedPage,
)


class _AnchorCollector(HTMLParser):
    """Collect bounded anchor href/text pairs without retaining a DOM."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._href: str | None = None
        self._text: list[str] = []
        self.anchors: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        href = next((value for key, value in attrs if key.casefold() == "href"), None)
        self._href = href.strip() if isinstance(href, str) else None
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None and len(self._text) < 8:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._href is None:
            return
        self.anchors.append((self._href, _SPACE.sub(" ", "".join(self._text)).strip()))
        self._href = None
        self._text = []


def _archive_links(
    html: str,
    *,
    base_urls: Sequence[str],
    years: Sequence[str],
    target: OfficialSourceTargetV1,
    resolver: OfficialSourceResolver,
    limit: int,
) -> tuple[str, ...]:
    """Resolve same-authority annual links selected only by frozen years."""

    if not html.strip() or not years or limit <= 0:
        return ()
    parser = _AnchorCollector()
    try:
        parser.feed(html)
    except Exception:
        return ()
    selected: list[str] = []
    seen: set[str] = set()
    for href, label in parser.anchors:
        compact_label = _SPACE.sub("", label)
        if not any(year in compact_label for year in years):
            continue
        for base_url in base_urls:
            resolved = urljoin(base_url, href)
            parsed = urlsplit(resolved)
            if parsed.scheme.casefold() not in {"http", "https"}:
                continue
            resolved = parsed._replace(fragment="").geturl()
            if resolved in seen or not resolver.verify(target, resolved).matched:
                continue
            seen.add(resolved)
            selected.append(resolved)
            # One anchor contributes one candidate.  Base order expresses the
            # preference; alternate bases are only fallbacks for resolution.
            break
        if len(selected) >= limit:
            break
    return tuple(selected)


def _archive_resolution_bases(
    candidate: _CandidatePlan,
    *,
    final_url: str,
) -> tuple[str, ...]:
    """Prefer registry canonical origins without probing host aliases."""

    requested = urlsplit(candidate.requested_url)
    bases: list[str] = []
    if isinstance(candidate.target, OfficialSourceTargetV1):
        for seed_url in candidate.target.seed_urls:
            seed = urlsplit(seed_url)
            # Preserve the fetched listing path/query while replacing only
            # the redirect-selected alias with the registry-declared origin.
            bases.append(
                requested._replace(
                    scheme=seed.scheme,
                    netloc=seed.netloc,
                    fragment="",
                ).geturl()
            )
    bases.extend((candidate.requested_url, final_url))
    return tuple(dict.fromkeys(base.strip() for base in bases if base.strip()))


class FetchMixin(EvidenceRuntimeCore):
    """Seed discovery, page fetches, durable page results and canonical page refs."""

    __slots__ = ()

    async def _discover_seed(
        self,
        candidate: _CandidatePlan,
        *,
        parent: DurableDeadlineV1,
        run_id: str,
        identity: NodeExecutionIdentity,
        semaphore: asyncio.Semaphore,
    ) -> _SeedDiscoveryOutcome:
        """Fetch an authority-owned index and follow year-labelled links only."""

        async with semaphore:
            span = await self._start_child_span(
                identity=identity,
                request_id=candidate.candidate_id,
                stage="official_archive_discovery",
                ordinal=candidate.ordinal,
            )

            async def _done(outcome: _SeedDiscoveryOutcome) -> _SeedDiscoveryOutcome:
                status = (
                    SpanStatus.OK if outcome.status in {"discovered", "empty"} else SpanStatus.ERROR
                )
                await self._finish_child_span(
                    span,
                    status,
                    outcome=outcome.status,
                    count=len(outcome.candidates),
                )
                return outcome

            try:
                logical_key = f"archive:{candidate.candidate_id}:{candidate.ordinal}"
                if self.durable_reads is not None:
                    child, lease = await self.durable_reads.deadline_port.resume_child(
                        identity=identity,
                        parent=parent,
                        logical_key=logical_key,
                        logical_scope="deep_research_v6.official_archive_discovery",
                        budget_ms=self.page_deadline_ms,
                        policy_hash=self._policy_hash,
                    )
                    transition = None
                else:
                    child = create_child_deadline(
                        parent,
                        logical_key=logical_key,
                        logical_scope="deep_research_v6.official_archive_discovery",
                        budget_ms=self.page_deadline_ms,
                        now_wall=self.wall_clock(),
                        policy_hash=self._policy_hash,
                    )
                    transition = resume_deadline(
                        child,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    lease = transition.lease
            except DeadlineValidationError:
                return await _done(_SeedDiscoveryOutcome("timeout", False))
            if lease is None:
                return await _done(_SeedDiscoveryOutcome("timeout", False))
            timeout_s = remaining_timeout_seconds(
                lease,
                now_monotonic_ns=self.monotonic_ns(),
                stage_cap_ms=self.page_deadline_ms,
            )
            if timeout_s <= 0:
                return await _done(_SeedDiscoveryOutcome("timeout", False))
            request = FetchRequest(
                url=candidate.requested_url,
                timeout=timeout_s,
                extract=True,
                render_policy="auto",
                include_html=True,
                max_chars=100_000,
                allow_jina=False,
                run_id=run_id,
                deadline_monotonic=lease.deadline_monotonic_ns / 1_000_000_000,
            )
            try:
                if self.durable_reads is None:
                    document = await asyncio.wait_for(
                        self.fetch_service.fetch(request), timeout=timeout_s
                    )
                else:
                    typed = await self._durable_page_result(
                        candidate=candidate,
                        request=request,
                        timeout_s=timeout_s,
                        deadline=child,
                        identity=identity,
                        prefer_html=True,
                    )
                    typed_ref = format_blob_ref(
                        hashlib.sha256(canonical_json(typed.to_json()).encode("utf-8")).hexdigest()
                    )
                    child, _ = await self.durable_reads.deadline_port.checkpoint(
                        identity=identity, state=child, lease=lease
                    )
                    if typed.outcome != "succeeded" or typed.page_record is None:
                        status = (
                            "timeout"
                            if typed.outcome == "timeout"
                            else "rejected"
                            if typed.outcome in {"blocked", "empty"}
                            else "failed"
                        )
                        return await _done(
                            _SeedDiscoveryOutcome(status, True, page_result_ref=typed_ref)
                        )
                    page = typed.page_record
                    raw_body = await self.blobs.get(str(page["body_ref"]).removeprefix("sha256:"))
                    raw_locator = await self.blobs.get(
                        typed.source_locator_ref.removeprefix("sha256:")
                    )
                    locator_value = json.loads(raw_locator.decode("utf-8"))
                    if not isinstance(locator_value, Mapping):
                        raise ValueError("source locator blob must be an object")
                    locator = SourceLocatorV1.from_json(locator_value)
                    body = raw_body.decode("utf-8")
                    document = EvidenceDocument(
                        stable_id=str(page["page_id"]),
                        url=locator.final_url,
                        canonical_url=locator.final_url,
                        title="",
                        text=body,
                        published_at=None,
                        provider="durable_v6",
                        providers=("durable_v6",),
                        provider_rank=0,
                        score=0.0,
                        searched_at="",
                        source_kind="official",
                        content_hash=str(page["body_hash"]),
                        fetcher="durable_v6",
                        extractor="durable_v6",
                        fetched_at=str(page["fetched_at"]),
                        html=body,
                    )
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._finish_child_span(span, SpanStatus.CANCELLED, outcome="cancelled")
                )
                raise
            except TimeoutError:
                return await _done(_SeedDiscoveryOutcome("timeout", True))
            except Exception:
                return await _done(_SeedDiscoveryOutcome("failed", True))
            if not isinstance(document, EvidenceDocument):
                return await _done(_SeedDiscoveryOutcome("failed", True))
            final_url = document.canonical_url.strip() or document.url.strip()
            if not self.source_resolver.verify(candidate.target, final_url).matched:
                return await _done(_SeedDiscoveryOutcome("rejected", True))
            links = _archive_links(
                document.html or "",
                # A redirect alias may be authority-valid but unusable for a
                # subsequent TLS connection.  Relative links keep the
                # registry canonical origin (or the original requested
                # origin when no seed exists); the final alias is a fallback.
                base_urls=_archive_resolution_bases(
                    candidate,
                    final_url=final_url,
                ),
                years=candidate.discovery_years,
                target=candidate.target,
                resolver=self.source_resolver,
                limit=self.max_fetches - 1,
            )
            discovered = tuple(
                _CandidatePlan(
                    ordinal=index,
                    dimension_id=candidate.dimension_id,
                    candidate_id=_stable_id(
                        "archive_",
                        {
                            "target_id": candidate.target.target_id,
                            "url": url,
                            "years": list(candidate.discovery_years),
                        },
                    ),
                    requested_url=url,
                    title=document.title or candidate.title,
                    target=candidate.target,
                )
                for index, url in enumerate(links)
            )
            return await _done(
                _SeedDiscoveryOutcome(
                    "discovered" if discovered else "empty",
                    True,
                    discovered,
                    typed_ref if self.durable_reads is not None else None,
                )
            )

    async def _fetch_one(
        self,
        candidate: _CandidatePlan,
        *,
        parent: DurableDeadlineV1,
        run_id: str,
        identity: NodeExecutionIdentity,
        semaphore: asyncio.Semaphore,
    ) -> _FetchOutcome:
        async with semaphore:
            fetch_span = await self._start_child_span(
                identity=identity,
                request_id=candidate.candidate_id,
                stage="page_fetch",
                ordinal=candidate.ordinal,
            )

            async def _done(outcome: _FetchOutcome) -> _FetchOutcome:
                status = SpanStatus.OK if outcome.status == "verified" else SpanStatus.ERROR
                await self._finish_child_span(
                    fetch_span,
                    status,
                    outcome=outcome.status,
                    count=1 if outcome.status == "verified" else 0,
                )
                return outcome

            try:
                await self._persist_locator(
                    candidate=candidate,
                    final_url=candidate.requested_url,
                    verification_status="unverified",
                    identity=identity,
                )
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._finish_child_span(fetch_span, SpanStatus.CANCELLED, outcome="cancelled")
                )
                raise
            except Exception:
                return await _done(_FetchOutcome(candidate.ordinal, "failed", False))
            try:
                logical_page_key = (
                    f"{candidate.dimension_id}:{candidate.candidate_id}:{candidate.ordinal}"
                )
                if self.durable_reads is not None:
                    child, lease = await self.durable_reads.deadline_port.resume_child(
                        identity=identity,
                        parent=parent,
                        logical_key=logical_page_key,
                        logical_scope="deep_research_v6.page_fetch",
                        budget_ms=self.page_deadline_ms,
                        policy_hash=self._policy_hash,
                    )
                    transition = None
                else:
                    child = create_child_deadline(
                        parent,
                        logical_key=logical_page_key,
                        logical_scope="deep_research_v6.page_fetch",
                        budget_ms=self.page_deadline_ms,
                        now_wall=self.wall_clock(),
                        policy_hash=self._policy_hash,
                    )
                    transition = resume_deadline(
                        child,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    lease = transition.lease
            except DeadlineValidationError:
                return await _done(_FetchOutcome(candidate.ordinal, "timeout", False))
            if lease is None:
                return await _done(_FetchOutcome(candidate.ordinal, "timeout", False))
            timeout_s = remaining_timeout_seconds(
                lease,
                now_monotonic_ns=self.monotonic_ns(),
                stage_cap_ms=self.page_deadline_ms,
            )
            if timeout_s <= 0:
                return await _done(_FetchOutcome(candidate.ordinal, "timeout", False))
            request = FetchRequest(
                url=candidate.requested_url,
                timeout=timeout_s,
                extract=True,
                render_policy="auto",
                include_html=False,
                max_chars=50_000,
                allow_jina=False,
                run_id=run_id,
                deadline_monotonic=lease.deadline_monotonic_ns / 1_000_000_000,
                prefer_httpx=True,
            )
            try:
                if self.durable_reads is None:
                    document = await asyncio.wait_for(
                        self.fetch_service.fetch(request), timeout=timeout_s
                    )
                else:
                    typed = await self._durable_page_result(
                        candidate=candidate,
                        request=request,
                        timeout_s=timeout_s,
                        deadline=child,
                        identity=identity,
                        prefer_html=False,
                    )
                    typed_ref = format_blob_ref(
                        hashlib.sha256(canonical_json(typed.to_json()).encode("utf-8")).hexdigest()
                    )
                    if typed.outcome == "timeout":
                        return await _done(
                            _FetchOutcome(
                                candidate.ordinal,
                                "timeout",
                                True,
                                page_result_ref=typed_ref,
                            )
                        )
                    if typed.outcome != "succeeded" or typed.page_record is None:
                        return await _done(
                            _FetchOutcome(
                                candidate.ordinal,
                                "rejected" if typed.outcome in {"blocked", "empty"} else "failed",
                                True,
                                page_result_ref=typed_ref,
                            )
                        )
                    page = typed.page_record
                    return await _done(
                        _FetchOutcome(
                            ordinal=candidate.ordinal,
                            status="verified",
                            upstream_called=True,
                            page=_VerifiedPage(
                                source_locator_ref=typed.source_locator_ref,
                                canonical_url_hash=str(page["canonical_url_hash"]),
                                final_url_hash=str(page["final_url_hash"]),
                                body_ref=str(page["body_ref"]),
                                body_hash=str(page["body_hash"]),
                                authority_id=str(page["authority_id"]),
                                title_hash=hashlib.sha256(
                                    (candidate.title or candidate.target.organization_name).encode(
                                        "utf-8"
                                    )
                                ).hexdigest(),
                                admission_reason=str(page["reason_codes"][0]),
                            ),
                            page_result_ref=typed_ref,
                        )
                    )
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._finish_child_span(fetch_span, SpanStatus.CANCELLED, outcome="cancelled")
                )
                raise
            except TimeoutError:
                return await _done(_FetchOutcome(candidate.ordinal, "timeout", True))
            except Exception:
                return await _done(_FetchOutcome(candidate.ordinal, "failed", True))
            if not isinstance(document, EvidenceDocument):
                return await _done(_FetchOutcome(candidate.ordinal, "failed", True))
            final_url = document.canonical_url.strip() or document.url.strip()
            if isinstance(candidate.target, OfficialSourceTargetV1):
                authority = self.source_resolver.verify(candidate.target, final_url)
                matched = authority.matched
                authority_id = authority.authority_id
                admission_reason = "official_domain_match"
            else:
                final_host = (urlsplit(final_url).hostname or "").casefold().rstrip(".")
                matched = bool(final_host) and final_host == candidate.target.requested_host
                authority_id = candidate.target.authority_id if matched else None
                admission_reason = "general_same_host_verified"
            verification_status = "verified" if matched else "rejected"
            try:
                locator = await self._persist_locator(
                    candidate=candidate,
                    final_url=final_url,
                    verification_status=verification_status,
                    identity=identity,
                )
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._finish_child_span(fetch_span, SpanStatus.CANCELLED, outcome="cancelled")
                )
                raise
            except Exception:
                return await _done(_FetchOutcome(candidate.ordinal, "failed", True))
            if not matched or authority_id is None:
                return await _done(_FetchOutcome(candidate.ordinal, "rejected", True))
            body = document.text
            if not body.strip():
                return await _done(_FetchOutcome(candidate.ordinal, "rejected", True))
            body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            title = (
                document.title.strip()
                or candidate.title.strip()
                or candidate.target.organization_name
            )
            try:
                body_blob = await self._put_blob(
                    body.encode("utf-8"),
                    identity,
                    media_type="text/plain; charset=utf-8",
                )
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._finish_child_span(fetch_span, SpanStatus.CANCELLED, outcome="cancelled")
                )
                raise
            except Exception:
                return await _done(_FetchOutcome(candidate.ordinal, "failed", True))
            if body_blob.sha256 != body_hash:
                return await _done(_FetchOutcome(candidate.ordinal, "failed", True))
            return await _done(
                _FetchOutcome(
                    ordinal=candidate.ordinal,
                    status="verified",
                    upstream_called=True,
                    page=_VerifiedPage(
                        source_locator_ref=locator["source_locator_ref"],
                        canonical_url_hash=locator["canonical_url_hash"],
                        final_url_hash=locator["final_url_hash"],
                        body_ref=format_blob_ref(body_blob.sha256),
                        body_hash=body_hash,
                        authority_id=authority_id,
                        title_hash=hashlib.sha256(title.encode("utf-8")).hexdigest(),
                        admission_reason=admission_reason,
                    ),
                )
            )

    async def _durable_page_result(
        self,
        *,
        candidate: _CandidatePlan,
        request: FetchRequest,
        timeout_s: float,
        deadline: DurableDeadlineV1,
        identity: NodeExecutionIdentity,
        prefer_html: bool,
    ) -> PageExtractionResultV1:
        """Own fetch, redirect validation and body persistence in one attempt."""

        if self.durable_reads is None:
            raise RuntimeError("durable page result requires the read-effect adapter")
        input_locator = await self._persist_locator(
            candidate=candidate,
            final_url=candidate.requested_url,
            verification_status="unverified",
            identity=identity,
        )

        async def _transport(attempt) -> PageExtractionResultV1:
            common = {
                "logical_page_id": candidate.candidate_id,
                "logical_effect_id": attempt.logical_effect_id,
                "attempt_no": attempt.attempt_no,
                "deadline_id": attempt.deadline_id,
                "control_command_id": None,
            }
            try:
                attempt_timeout_s = timeout_s
                if attempt.deadline_monotonic_ns is not None:
                    attempt_timeout_s = min(
                        attempt_timeout_s,
                        max(
                            0.0,
                            (attempt.deadline_monotonic_ns - self.monotonic_ns()) / 1_000_000_000,
                        ),
                    )
                document = await asyncio.wait_for(
                    self.fetch_service.fetch(request), timeout=attempt_timeout_s
                )
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                return PageExtractionResultV1.create(
                    **common,
                    source_locator_ref=input_locator["source_locator_ref"],
                    page_record=None,
                    spans=(),
                    bindings=(),
                    outcome="timeout",
                    error_code="deadline_expired",
                )
            except Exception:
                return PageExtractionResultV1.create(
                    **common,
                    source_locator_ref=input_locator["source_locator_ref"],
                    page_record=None,
                    spans=(),
                    bindings=(),
                    outcome="failed",
                    error_code="fetch_failed",
                )
            if not isinstance(document, EvidenceDocument):
                return PageExtractionResultV1.create(
                    **common,
                    source_locator_ref=input_locator["source_locator_ref"],
                    page_record=None,
                    spans=(),
                    bindings=(),
                    outcome="failed",
                    error_code="invalid_fetch_result",
                )
            final_url = document.canonical_url.strip() or document.url.strip()
            if isinstance(candidate.target, OfficialSourceTargetV1):
                authority = self.source_resolver.verify(candidate.target, final_url)
                matched = authority.matched
                authority_id = authority.authority_id or candidate.target.authority_id
                admission_reason = "official_domain_match"
                source_tier = "first_party"
            else:
                final_host = (urlsplit(final_url).hostname or "").casefold().rstrip(".")
                matched = bool(final_host) and final_host == candidate.target.requested_host
                authority_id = candidate.target.authority_id
                admission_reason = "general_same_host_verified"
                source_tier = "general"
            locator = await self._persist_locator(
                candidate=candidate,
                final_url=final_url,
                verification_status="verified" if matched else "rejected",
                identity=identity,
            )
            if not matched:
                return PageExtractionResultV1.create(
                    **common,
                    source_locator_ref=locator["source_locator_ref"],
                    page_record=None,
                    spans=(),
                    bindings=(),
                    outcome="blocked",
                    error_code="redirect_authority_rejected",
                )
            body = (document.html if prefer_html and document.html else document.text).strip()
            if not body:
                return PageExtractionResultV1.create(
                    **common,
                    source_locator_ref=locator["source_locator_ref"],
                    page_record=None,
                    spans=(),
                    bindings=(),
                    outcome="empty",
                    error_code=None,
                )
            body_bytes = body.encode("utf-8")
            body_hash = hashlib.sha256(body_bytes).hexdigest()
            body_blob = await self._put_blob(
                body_bytes,
                identity,
                media_type="text/html; charset=utf-8"
                if prefer_html
                else "text/plain; charset=utf-8",
            )
            if body_blob.sha256 != body_hash:
                raise ValueError("registered page body digest mismatch")
            page_id = (
                "page_"
                + hashlib.sha256(
                    (locator["final_url_hash"] + body_hash).encode("ascii")
                ).hexdigest()[:24]
            )
            page_record: dict[str, JsonValue] = {
                "schema_version": 1,
                "page_id": page_id,
                "source_locator_ref": locator["source_locator_ref"],
                "canonical_url_hash": locator["canonical_url_hash"],
                "final_url_hash": locator["final_url_hash"],
                "authority_id": authority_id,
                "source_family_id": authority_id,
                "source_tier": source_tier,
                "body_ref": format_blob_ref(body_blob.sha256),
                "body_hash": body_hash,
                "fetched_at": document.fetched_at or datetime.now(UTC).isoformat(),
                "media_type": "text/html; charset=utf-8"
                if prefer_html
                else "text/plain; charset=utf-8",
                "admission_status": "admitted",
                "reason_codes": [admission_reason],
            }
            return PageExtractionResultV1.create(
                **common,
                source_locator_ref=locator["source_locator_ref"],
                page_record=page_record,
                spans=(),
                bindings=(),
                outcome="succeeded",
                error_code=None,
            )

        raw = await self.durable_reads.execute(
            operation_kind="page_fetch",
            route_id=str(self._active_route_id),
            target_or_page_id=candidate.candidate_id,
            ordinal=candidate.ordinal,
            resource_kind="fetch",
            resource_hard_limit=int(self._active_route_budget["fetch"]),
            resource_policy_hash=self._policy_hash,
            deadline=deadline,
            identity=identity,
            transport=_transport,
            input_source_locator_ref=input_locator["source_locator_ref"],
        )
        return PageExtractionResultV1.from_json(raw)

    async def _persist_locator(
        self,
        *,
        candidate: _CandidatePlan,
        final_url: str,
        verification_status: Literal["verified", "rejected", "unverified"],
        identity: NodeExecutionIdentity,
    ) -> dict[str, str]:
        canonical_url = candidate.requested_url
        if verification_status == "unverified":
            final_url = canonical_url
        canonical_url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        final_url_hash = hashlib.sha256(final_url.encode("utf-8")).hexdigest()
        locator = SourceLocatorV1.create(
            canonical_url=canonical_url,
            final_url=final_url,
            canonical_url_hash=canonical_url_hash,
            final_url_hash=final_url_hash,
            authority_id=candidate.target.authority_id,
            verification_status=verification_status,
            verification_policy_hash=(
                self.source_resolver.registry.policy_hash
                if isinstance(candidate.target, OfficialSourceTargetV1)
                else candidate.target.verification_policy_hash
            ),
            redirect_chain_hashes=(
                () if final_url_hash == canonical_url_hash else (final_url_hash,)
            ),
        )
        blob = await self._put_blob(
            canonical_json(locator.to_json()).encode("utf-8"),
            identity,
            media_type="application/vnd.deskpet.source-locator.v1+json",
        )
        return {
            "source_locator_ref": format_blob_ref(blob.sha256),
            "canonical_url_hash": canonical_url_hash,
            "final_url_hash": final_url_hash,
        }

    async def _put_blob(
        self,
        data: bytes,
        identity: NodeExecutionIdentity,
        *,
        media_type: str,
    ) -> BlobRef:
        async with self._blob_put_lock:
            return await self.blobs.put(
                data,
                identity,
                media_type=media_type,
            )

    @staticmethod
    def _canonical_page_result_refs(
        outcomes: Sequence[_FetchOutcome],
    ) -> tuple[str, ...]:
        refs: list[str] = []
        seen_final_urls: set[str] = set()
        seen_refs: set[str] = set()
        for outcome in sorted(outcomes, key=lambda item: item.ordinal):
            if outcome.page_result_ref is None or outcome.page_result_ref in seen_refs:
                continue
            if outcome.status == "verified" and outcome.page is not None:
                if outcome.page.final_url_hash in seen_final_urls:
                    continue
                seen_final_urls.add(outcome.page.final_url_hash)
            seen_refs.add(outcome.page_result_ref)
            refs.append(outcome.page_result_ref)
        return tuple(refs)

    @staticmethod
    def _canonical_pages(
        outcomes: Sequence[_FetchOutcome],
    ) -> tuple[V6FetchedPageRefPayloadV1, ...]:
        pages: list[V6FetchedPageRefPayloadV1] = []
        seen_final_urls: set[str] = set()
        for outcome in sorted(outcomes, key=lambda item: item.ordinal):
            page = outcome.page
            if outcome.status != "verified" or page is None:
                continue
            if page.final_url_hash in seen_final_urls:
                continue
            seen_final_urls.add(page.final_url_hash)
            page_id = (
                "page_"
                + hashlib.sha256(
                    (page.final_url_hash + page.body_hash).encode("ascii")
                ).hexdigest()[:24]
            )
            pages.append(
                V6FetchedPageRefPayloadV1(
                    page_id=page_id,
                    ordinal=len(pages),
                    source_locator_ref=page.source_locator_ref,
                    body_ref=page.body_ref,
                    body_hash=page.body_hash,
                    canonical_url_hash=page.canonical_url_hash,
                    final_url_hash=page.final_url_hash,
                    authority_id=page.authority_id,
                    title_hash=page.title_hash,
                    media_type="text/plain; charset=utf-8",
                    admission_status="admitted",
                    reason_codes=(page.admission_reason,),
                )
            )
        return tuple(pages)
