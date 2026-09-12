from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from config import SearchGatewayConfig
from deskpet.retrieval.contracts import (
    FetchRequest,
    RetrievalCandidate,
    SearchRequest,
)
from deskpet.retrieval.fetch_extract import FetchExtractService
from deskpet.retrieval.playwright_renderer import (
    FetchRenderBudget,
    PlaywrightRenderError,
    PlaywrightRendererPool,
)
from deskpet.retrieval.search_gateway import SearchGateway
from deskpet.workflows.contracts import WorkflowContext
from deskpet.workflows.definitions.deep_research_v5_contracts import DimensionCoverage
from deskpet.workflows.definitions.v5 import (
    DEEP_RESEARCH_V5,
    deep_research_initial_state,
)
from deskpet.workflows.native import InMemoryNativeCheckpointStore


@pytest.mark.parametrize("system,machine", [("win32", "AMD64"), ("darwin", "arm64")])
def test_native_bundle_resolver_selects_only_platform_executable(tmp_path, system, machine):
    from deskpet.playwright_bundle import get_platform_contract
    from deskpet.retrieval.playwright_renderer import resolve_browser_executable

    contract = get_platform_contract(system, machine)
    revision = tmp_path / contract.revision_dir
    executable = revision / contract.executable_relative
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    (revision / "INSTALLATION_COMPLETE").touch()
    other = get_platform_contract("darwin", "arm64") if system == "win32" else get_platform_contract("win32", "AMD64")
    foreign = revision / other.executable_relative
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"wrong platform")
    assert resolve_browser_executable(tmp_path, contract=contract) == executable
    executable.unlink()
    with pytest.raises(PlaywrightRenderError, match="browser_bundle_ambiguous:0"):
        resolve_browser_executable(tmp_path, contract=contract)


@pytest.mark.parametrize("system,machine", [("win32", "AMD64"), ("darwin", "arm64")])
def test_native_bundle_resolver_rejects_ambiguous_complete_revisions(tmp_path, system, machine):
    from deskpet.playwright_bundle import get_platform_contract
    from deskpet.retrieval.playwright_renderer import resolve_browser_executable

    contract = get_platform_contract(system, machine)
    for revision in (contract.revision_dir, "chromium_headless_shell-9999"):
        root = tmp_path / revision
        executable = root / contract.executable_relative
        executable.parent.mkdir(parents=True)
        executable.touch()
        (root / "INSTALLATION_COMPLETE").touch()
    with pytest.raises(PlaywrightRenderError, match="browser_bundle_ambiguous:2"):
        resolve_browser_executable(tmp_path, contract=contract)


class _FixtureHandler(BaseHTTPRequestHandler):
    posts = 0

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        if self.path == "/slow":
            time.sleep(1.0)
        if self.path == "/state":
            body = """
                <script>
                document.body.textContent = localStorage.getItem('secret') || 'fresh-context';
                localStorage.setItem('secret', 'leaked-context');
                document.cookie = 'secret=leaked';
                </script>
            """
        elif self.path == "/post":
            body = """
                <div id="result">read-only</div>
                <script>fetch('/write', {method: 'POST', body: 'forbidden'}).catch(() => {});</script>
            """
        elif self.path == "/captcha":
            body = "<article>Security verification CAPTCHA: verify you are human.</article>"
        elif self.path == "/security":
            body = "<article>Security verification is required before access.</article>"
        elif self.path == "/login":
            body = "<article>Sign in to continue. Login required.</article>"
        elif self.path == "/app-shell":
            body = "<div id='app'></div>" + "<script>window.shell=true;</script>" * 100
        elif self.path == "/cross-origin-on-scroll":
            target = f"http://localhost:{self.server.server_port}/dynamic"
            body = f"""
                <article style='height:4000px'>origin fence fixture</article>
                <script>window.scrollBy = () => {{ window.location.href = {target!r}; }};</script>
            """
        else:
            body = """
                <div id="app"></div>
                <script>
                document.getElementById('app').innerHTML =
                  '<article><h1>Rendered fixture</h1><p>' +
                  'dynamic evidence '.repeat(120) + '</p></article>';
                </script>
            """
        payload = (
            "<!doctype html><html><head><title>Fixture</title></head><body>"
            + body
            + "</body></html>"
        ).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        type(self).posts += 1
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return


@pytest.fixture(scope="module")
def fixture_server():
    _FixtureHandler.posts = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture(scope="module")
def task0_browser_root() -> Path:
    explicit_owner = os.environ.get("DESKPET_TEST_PLAYWRIGHT_OWNER")
    if explicit_owner:
        from deskpet.playwright_bundle import validate_browser_owner

        root = Path(explicit_owner).resolve()
        validate_browser_owner(root)
        return root
    evidence = (
        Path(__file__).parents[2]
        / "plans"
        / "2026-07-16-deepresearch-playwright-quality"
        / "evidence"
        / "win11"
        / "gate-a-audit.json"
    )
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    root = Path(payload["published_registry"]["root"]).parent
    executable = root / payload["published_registry"]["layout"]
    if not executable.is_file():
        pytest.skip("Task 0 isolated browser cache is not present")
    return root


def _pool(root: Path, **kwargs) -> PlaywrightRendererPool:
    return PlaywrightRendererPool(
        browser_root_resolver=lambda: root,
        idle_shutdown_s=kwargs.pop("idle_shutdown_s", 30.0),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_real_task0_browser_renders_dynamic_page_with_isolated_contexts(
    fixture_server: str, task0_browser_root: Path
) -> None:
    pool = _pool(task0_browser_root)
    try:
        # The first real Chromium launch can be delayed by Windows Defender
        # on a cold cache.  This integration case validates rendering and
        # context isolation, not the production per-fetch latency policy.
        dynamic = await pool.render(f"{fixture_server}/dynamic", timeout=30.0)
        first_state = await pool.render(f"{fixture_server}/state", timeout=30.0)
        second_state = await pool.render(f"{fixture_server}/state", timeout=30.0)
        await pool.render(f"{fixture_server}/post", timeout=30.0)
        await asyncio.sleep(0.1)
    finally:
        await pool.close()

    assert "dynamic evidence" in dynamic.html
    assert "fresh-context" in first_state.html
    assert "fresh-context" in second_state.html
    assert "leaked-context" not in second_state.html
    assert _FixtureHandler.posts == 0


@pytest.mark.asyncio
async def test_real_pool_caps_page_context_concurrency_at_two(
    fixture_server: str, task0_browser_root: Path
) -> None:
    pool = _pool(task0_browser_root)
    try:
        results = await asyncio.gather(
            *(pool.render(f"{fixture_server}/dynamic") for _ in range(6))
        )
    finally:
        await pool.close()

    assert len(results) == 6
    assert pool.peak_contexts == 2
    assert pool.active_contexts == 0


@pytest.mark.asyncio
async def test_timeout_and_cancel_close_real_page_and_context(
    fixture_server: str, task0_browser_root: Path
) -> None:
    pool = _pool(task0_browser_root)
    try:
        with pytest.raises(PlaywrightRenderError, match="timeout"):
            await pool.render(f"{fixture_server}/slow", timeout=0.15)

        cancel = asyncio.Event()
        task = asyncio.create_task(
            pool.render(f"{fixture_server}/slow", timeout=3.0, cancel_event=cancel)
        )
        while pool.active_contexts == 0:
            await asyncio.sleep(0.01)
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pool.active_contexts == 0
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
async def test_real_browser_crash_restarts_once_and_preserves_cleanup(
    fixture_server: str, task0_browser_root: Path
) -> None:
    import psutil

    from deskpet.playwright_bundle import get_platform_contract

    contract = get_platform_contract()
    executable = str(
        task0_browser_root
        / contract.revision_dir
        / contract.executable_relative
    ).casefold()

    def root_processes() -> dict[int, object]:
        result = {}
        for process in psutil.process_iter(("exe", "cmdline")):
            try:
                if str(process.info["exe"] or "").casefold() != executable:
                    continue
                command = " ".join(process.info["cmdline"] or ())
                if "--type=" not in command:
                    result[process.pid] = process
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        return result

    before = set(root_processes())
    pool = _pool(task0_browser_root)
    try:
        task = asyncio.create_task(pool.render(f"{fixture_server}/slow", timeout=8.0))
        while pool.active_contexts == 0 or pool._browser is None:
            await asyncio.sleep(0.01)
        roots = root_processes()
        owned_roots = [process for pid, process in roots.items() if pid not in before]
        assert len(owned_roots) == 1
        owned_roots[0].kill()
        result = await task
    finally:
        await pool.close()

    assert "dynamic evidence" in result.html
    assert result.browser_restarted is True
    assert pool.active_contexts == 0


@pytest.mark.asyncio
async def test_idle_shutdown_reaps_browser_and_driver_children(
    fixture_server: str, task0_browser_root: Path
) -> None:
    import psutil

    def children() -> dict[int, bool]:
        root_text = str(task0_browser_root).casefold()
        result = {}
        for process in psutil.process_iter(("exe", "cmdline")):
            try:
                command = " ".join(process.info["cmdline"] or ()).replace("\\", "/").casefold()
                executable = str(process.info["exe"] or "").casefold()
                is_driver = "playwright/driver/package/cli.js run-driver" in command
                if root_text in executable or is_driver:
                    result[process.pid] = is_driver
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        return result

    pool = _pool(task0_browser_root, idle_shutdown_s=0.05)
    try:
        await pool.render(f"{fixture_server}/dynamic")
        owned = dict(pool._owned_processes)
        observed = children()
        drivers = {pid for pid in owned if observed.get(pid) is True}
        browsers = {pid for pid in owned if observed.get(pid) is False}
        assert drivers, "the real Playwright Node driver must be owned before cleanup"
        assert browsers, "the real browser must be owned before cleanup"
        assert any(psutil.Process(pid).ppid() in drivers for pid in browsers)
        idle = pool._idle_task
        assert idle is not None
        await asyncio.wait_for(asyncio.shield(idle), timeout=8.0)
        assert pool._browser is None
        assert pool._playwright is None
    finally:
        await pool.close()

    def alive_owned() -> set[int]:
        # Track the captured identities directly. A changed command line or a
        # broken path matcher must not turn the cleanup assertion into a pass.
        alive = set()
        for pid, created in owned.items():
            try:
                process = psutil.Process(pid)
                if abs(process.create_time() - created) < 0.001 and process.is_running():
                    alive.add(pid)
            except psutil.NoSuchProcess:
                pass
        return alive

    for _ in range(250):
        if not alive_owned():
            break
        await asyncio.sleep(0.02)
    assert alive_owned() == set()


@pytest.mark.parametrize("driver_script", [
    r"C:\venv\Lib\site-packages\playwright\driver\package\cli.js",
    "/venv/lib/site-packages/playwright/driver/package/cli.js",
])
def test_process_snapshot_tracks_native_driver_and_owns_only_browser_ancestor(monkeypatch, driver_script):
    from types import SimpleNamespace
    import psutil

    executable = Path("/owned/chrome-headless-shell")
    rows = [
        SimpleNamespace(pid=10, info={"exe": str(executable), "cmdline": [str(executable)], "create_time": 10.0, "ppid": 20}),
        SimpleNamespace(pid=20, info={"exe": "/venv/node", "cmdline": ["node", driver_script, "run-driver"], "create_time": 9.0, "ppid": 1}),
        SimpleNamespace(pid=30, info={"exe": "/venv/node", "cmdline": ["node", driver_script, "run-driver"], "create_time": 9.5, "ppid": 1}),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda _attrs: rows)
    snapshot = PlaywrightRendererPool._process_snapshot(executable)
    assert snapshot[20] == (9.0, False, True, 1)
    assert snapshot[30] == (9.5, False, True, 1)
    pool = PlaywrightRendererPool(executable_resolver=lambda: executable)
    pool._capture_owned_processes(executable, {})
    assert pool._owned_processes == {10: 10.0, 20: 9.0}


@pytest.mark.asyncio
async def test_zero_idle_shutdown_cannot_close_browser_starting_next_context(
    fixture_server: str, task0_browser_root: Path
) -> None:
    pool = _pool(task0_browser_root, idle_shutdown_s=0.0)
    try:
        for _ in range(3):
            await pool.render(f"{fixture_server}/dynamic")
            await asyncio.sleep(0)
            results = await asyncio.gather(
                pool.render(f"{fixture_server}/dynamic"),
                pool.render(f"{fixture_server}/dynamic"),
            )
            assert all("dynamic evidence" in result.html for result in results)
    finally:
        await pool.close()

    assert pool.active_contexts == 0


@pytest.mark.asyncio
async def test_delayed_navigation_is_checked_after_non_navigation_action(
    fixture_server: str, task0_browser_root: Path
) -> None:
    pool = _pool(task0_browser_root)
    try:
        with pytest.raises(PlaywrightRenderError, match="cross_origin_navigation"):
            await pool.render(
                f"{fixture_server}/cross-origin-on-scroll",
                actions=("bounded_scroll",),
            )
    finally:
        await pool.close()


def test_process_ownership_excludes_unrelated_contemporaneous_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "chrome-headless-shell.exe"
    executable.write_bytes(b"fixture")
    pool = PlaywrightRendererPool(executable_resolver=lambda: executable)
    after = {
        10: (10.0, True, False, 20),
        20: (9.0, False, True, 1),
        30: (11.0, False, True, 1),
    }
    monkeypatch.setattr(pool, "_process_snapshot", lambda _executable: after)

    pool._capture_owned_processes(executable, {})

    assert pool._owned_processes == {10: 10.0, 20: 9.0}


@pytest.mark.asyncio
async def test_cancel_during_playwright_start_closes_partial_manager(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "chrome-headless-shell.exe"
    executable.write_bytes(b"fixture")

    class BlockingManager:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.closed = asyncio.Event()

        async def start(self):
            self.started.set()
            await asyncio.Future()

        async def __aexit__(self, *args):
            self.closed.set()

    manager = BlockingManager()
    pool = PlaywrightRendererPool(
        executable_resolver=lambda: executable,
        playwright_factory=lambda: manager,
    )
    cancel = asyncio.Event()
    task = asyncio.create_task(
        pool.render("https://example.test/", cancel_event=cancel, timeout=5.0)
    )
    await manager.started.wait()
    cancel.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.closed.is_set()
    await pool.close()


class _StaticTransport:
    def __init__(self, html: str) -> None:
        self.html = html
        self.calls = 0

    def fetch(self, url: str, *, timeout: float) -> dict[str, object]:
        self.calls += 1
        return {
            "ok": True,
            "status": 200,
            "html": self.html,
            "content_type": "text/html",
            "url_final": url,
            "fetcher": "static",
        }


def _shell() -> str:
    return (
        "<html><body><div id='app'></div>"
        + "<script>window.__shell=true;</script>" * 100
        + "</body></html>"
    )


@pytest.mark.asyncio
async def test_fetch_keeps_static_first_and_uses_playwright_only_for_shell(
    fixture_server: str, task0_browser_root: Path
) -> None:
    pool = _pool(task0_browser_root)
    normal = "<html><body><article>" + "static evidence " * 100 + "</article></body></html>"
    static_service = FetchExtractService(
        transport=_StaticTransport(normal),
        client=httpx.AsyncClient(),
        respect_robots=False,
        request_interval_ms=0,
        playwright_renderer=pool,
        render_call=AsyncMock(side_effect=AssertionError("Edge must not run")),
    )
    try:
        static_doc = await static_service.fetch(
            FetchRequest(f"{fixture_server}/dynamic", render_policy="auto")
        )
        assert static_doc.fetcher == "static"
        assert pool._browser is None

        static_service.transport = _StaticTransport(_shell())
        dynamic_doc = await static_service.fetch(
            FetchRequest(f"{fixture_server}/dynamic?second=1", render_policy="auto")
        )
        assert dynamic_doc.fetcher == "playwright"
        assert "dynamic evidence" in dynamic_doc.text
    finally:
        await static_service.close()
        await static_service.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "flag"),
    [
        ("captcha", "captcha"),
        ("security", "security_verification"),
        ("login", "login_wall"),
        ("app-shell", "app_shell"),
    ],
)
async def test_rendered_verification_pages_are_invalid_and_never_reach_edge(
    path: str,
    flag: str,
    fixture_server: str,
    task0_browser_root: Path,
) -> None:
    pool = _pool(task0_browser_root)
    edge = AsyncMock(side_effect=AssertionError("invalid evidence must not reach Edge"))
    service = FetchExtractService(
        transport=_StaticTransport(_shell()),
        client=httpx.AsyncClient(),
        respect_robots=False,
        request_interval_ms=0,
        playwright_renderer=pool,
        render_call=edge,
    )
    try:
        document = await service.fetch(FetchRequest(f"{fixture_server}/{path}"))
    finally:
        await service.close()
        await service.client.aclose()

    assert document.fetcher == "playwright"
    assert flag in document.quality_flags
    edge.assert_not_awaited()


@pytest.mark.asyncio
async def test_static_transport_failure_can_recover_through_playwright(
    fixture_server: str, task0_browser_root: Path
) -> None:
    class FailedTransport:
        def fetch(self, url: str, *, timeout: float):
            return {"ok": False, "error": "static_failed"}

    async def fail_httpx(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("static failed", request=request)

    pool = _pool(task0_browser_root)
    edge = AsyncMock(side_effect=AssertionError("Playwright recovery must stop fallback"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(fail_httpx))
    service = FetchExtractService(
        transport=FailedTransport(),
        client=client,
        respect_robots=False,
        request_interval_ms=0,
        playwright_renderer=pool,
        render_call=edge,
    )
    try:
        document = await service.fetch(FetchRequest(f"{fixture_server}/dynamic"))
    finally:
        await service.close()
        await client.aclose()

    assert document.fetcher == "playwright"
    assert "dynamic evidence" in document.text
    edge.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_gateway_fetch_playwright_chain_feeds_v5_graph(
    fixture_server: str, task0_browser_root: Path
) -> None:
    """One regression crosses the production component boundaries end to end.

    The provider is deterministic and local, but the SearchGateway allocator,
    FetchExtractService policy, bundled Playwright browser, and v5 graph are
    the real implementations.  This prevents individually green component
    tests from hiding a broken result/fetch/coverage contract between layers.
    """

    class FixtureProvider:
        name = "duckduckgo"
        capabilities = frozenset({"html"})

        def __init__(self) -> None:
            self.calls = 0

        async def is_available(self) -> bool:
            return True

        async def search(self, request, budget, client):
            del budget, client
            self.calls += 1
            url = f"{fixture_server}/dynamic"
            return [
                RetrievalCandidate(
                    stable_id="fixture-dynamic",
                    url=url,
                    canonical_url=url,
                    title=f"{request.query} dynamic source",
                    snippet="A deterministic dynamic evidence source.",
                    provider=self.name,
                    providers=(self.name,),
                    provider_rank=1,
                    searched_at="2026-07-16T00:00:00+00:00",
                )
            ]

    provider = FixtureProvider()
    gateway_client = httpx.AsyncClient()
    gateway = SearchGateway(
        config=replace(
            SearchGatewayConfig(),
            providers=[provider.name],
            research_target_results=1,
            research_target_domains=1,
        ),
        providers=[provider],
        client=gateway_client,
    )
    pool = _pool(task0_browser_root)
    fetch_client = httpx.AsyncClient()
    service = FetchExtractService(
        transport=_StaticTransport(_shell()),
        client=fetch_client,
        respect_robots=False,
        request_interval_ms=0,
        playwright_renderer=pool,
        render_call=AsyncMock(
            side_effect=AssertionError("valid bundled Playwright output must stop Edge fallback")
        ),
    )

    class SearchStage:
        async def execute(self, *, stage, payload, identity):
            del identity
            if stage == "direct":
                return {}
            if stage == "gap_work":
                query = str(payload["work_item"]["query"])
            else:
                query = str(payload["initial_queries"][0]["query"])
            return (
                await gateway.search(
                    SearchRequest(
                        query,
                        max_results=3,
                        mode="research",
                        run_id="run-v5-real-retrieval",
                    )
                )
            ).to_dict()

    class FetchStage:
        fetcher = ""

        async def execute(self, *, stage, payload, identity):
            del identity
            assert stage == "fetch"
            rows = payload["search_result"]["results"]
            document = await service.fetch(
                FetchRequest(str(rows[0]["url"]), render_policy="auto")
            )
            self.fetcher = document.fetcher
            coverages = []
            candidates = []
            for item in payload["research_brief"]["dimensions"]:
                dimension_id = str(item["dimension_id"])
                ids = (
                    f"{document.content_hash}-{dimension_id}-1",
                    f"{document.content_hash}-{dimension_id}-2",
                )
                coverages.append(
                    DimensionCoverage(
                        dimension_id,
                        "covered",
                        ids,
                        ids,
                        (f"fixture-family-{dimension_id}-1", f"fixture-family-{dimension_id}-2"),
                        True,
                        0.95,
                    ).to_json()
                )
                for index, candidate_id in enumerate(ids, 1):
                    candidates.append(
                        {
                            "candidate_id": candidate_id,
                            "dimension_id": dimension_id,
                            "url": document.url,
                            "canonical_url": document.canonical_url,
                            "title": document.title,
                            "body_text": document.text,
                            "span_text": "dynamic evidence",
                            "relevance": 0.95,
                            "source_type": "official_document",
                            "source_tier": "first_party",
                            "content_hash": document.content_hash,
                            "published_date": None,
                            "invalid_reason": None,
                            "page_flags": [],
                            "family_id": f"fixture-family-{dimension_id}-{index}",
                            "admitted": True,
                        }
                    )
            return {
                "documents": [document.to_dict()],
                "coverages": coverages,
                "evidence_candidates": candidates,
                "passage_blob_refs": [document.content_hash],
                "quality_score": 95.0,
            }

    class LLMStage:
        async def execute(self, *, stage, payload, identity):
            del identity
            if stage == "report_synthesis":
                analyses = []
                claims = []
                for index, coverage in enumerate(payload["dimension_coverages"]):
                    dimension_id = str(coverage["dimension_id"])
                    winning = list(coverage["winning_evidence_ids"])
                    analyses.append(
                        {
                            "schema_version": 1,
                            "dimension_id": dimension_id,
                            "direct_answer": "dynamic evidence",
                            "fact_claim_ids": [f"fact-{dimension_id}"],
                            "inference_claim_ids": [],
                            "limitation_claim_ids": [],
                            "winning_evidence_ids": winning,
                            "relevance_score": 0.95,
                            "confidence": "high",
                        }
                    )
                    for kind in ("current_state", "driver_change", "impact"):
                        claims.append(
                            {
                                "claim_id": f"{kind}-{dimension_id}",
                                "dimension_id": dimension_id,
                                "text": "dynamic evidence",
                                "kind": kind,
                                "source_ids": winning[:1],
                                "supported_fact_refs": winning[:1],
                                "intent_tokens": ["教育"],
                                "is_inference": False,
                                "metadata_pseudo_judgment": False,
                            }
                        )
                    if index < 6:
                        claims.append(
                            {
                                "claim_id": f"key-{dimension_id}",
                                "dimension_id": dimension_id,
                                "text": "dynamic evidence",
                                "kind": "key_judgment",
                                "source_ids": winning[:1],
                                "supported_fact_refs": winning[:1],
                                "intent_tokens": ["教育"],
                                "is_inference": False,
                                "metadata_pseudo_judgment": False,
                            }
                        )
                return {
                    "analyses": analyses,
                    "claims": claims,
                }
            if stage == "quality_audit":
                return {"route": "persist"}
            return {}

    class ArtifactStage:
        async def execute(self, *, stage, payload, identity):
            del payload, identity
            assert stage == "persist"
            return {"report_ref": "artifact:dynamic-evidence-report"}

    fetch_stage = FetchStage()
    executable = DEEP_RESEARCH_V5.bind(
        checkpointer=InMemoryNativeCheckpointStore()
    )
    state = deep_research_initial_state(
        topic="中国小学教育动态页面证据",
        run_id="run-v5-real-retrieval",
        thread_id="thread-v5-real-retrieval",
    )
    try:
        result = await executable.ainvoke(
            state,
            WorkflowContext(
                ports={
                    "llm": LLMStage(),
                    "search": SearchStage(),
                    "fetch": fetch_stage,
                    "artifact": ArtifactStage(),
                }
            ),
            thread_id="thread-v5-real-retrieval",
            run_id="run-v5-real-retrieval",
        )
    finally:
        await service.close()
        await fetch_client.aclose()
        await gateway.shutdown()
        await gateway_client.aclose()

    assert provider.calls == 1
    assert fetch_stage.fetcher == "playwright"
    assert result["values"]["terminal_public"] == {"delivery_status": "completed"}
    assert result["values"]["report_ref"] == "artifact:dynamic-evidence-report"


@pytest.mark.asyncio
async def test_playwright_failure_falls_to_existing_edge_without_jina() -> None:
    class FailedPool:
        async def render(self, *args, **kwargs):
            raise PlaywrightRenderError("launch_failed")

        async def close(self):
            return None

    rendered = "<html><body><article>" + "edge evidence " * 100 + "</article></body></html>"
    edge = AsyncMock(return_value=rendered)
    service = FetchExtractService(
        transport=_StaticTransport(_shell()),
        client=httpx.AsyncClient(),
        respect_robots=False,
        request_interval_ms=0,
        playwright_renderer=FailedPool(),
        render_call=edge,
        allow_jina=True,
    )
    service._jina = AsyncMock(side_effect=AssertionError("Jina is not enabled by request"))
    budget = FetchRenderBudget()
    try:
        document = await service.fetch(
            FetchRequest("https://fallback.test/", render_budget=budget, allow_jina=False)
        )
        snapshot = await budget.snapshot()
    finally:
        await service.close()
        await service.client.aclose()

    assert document.fetcher == "cdp-edge"
    assert snapshot["claimed"] == {"static": 1, "playwright": 1, "edge": 1}
    edge.assert_awaited_once()
