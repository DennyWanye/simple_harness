"""Retrieval-owned, bounded Playwright renderer.

The pool never discovers or downloads a browser.  A packaging-owned resolver
must inject either the exact executable or an isolated browser registry root.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit


class ReadOnlyAction(str, Enum):
    GOTO = "goto"
    WAIT_FOR_BODY = "wait_for_body"
    BOUNDED_SCROLL = "bounded_scroll"
    EXPAND_READ_MORE = "expand_read_more"
    NEXT_PAGE = "next_page"


class PlaywrightRenderError(RuntimeError):
    def __init__(self, code: str, *, retriable: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.retriable = retriable


@dataclass(frozen=True, slots=True)
class RenderPagePart:
    page_part_id: str
    page_index: int
    final_url: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class RenderOutcome:
    html: str
    final_url: str
    page_parts: tuple[RenderPagePart, ...]
    browser_restarted: bool = False


class FetchRenderBudget:
    """Per-fetch attempt budget; stages cannot consume each other's quota."""

    def __init__(
        self,
        *,
        static_attempts: int = 1,
        playwright_attempts: int = 1,
        edge_attempts: int = 1,
    ) -> None:
        values = (static_attempts, playwright_attempts, edge_attempts)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("render attempt budgets must be non-negative integers")
        self._remaining = {
            "static": static_attempts,
            "playwright": playwright_attempts,
            "edge": edge_attempts,
        }
        self._claimed = {"static": 0, "playwright": 0, "edge": 0}
        self._lock = asyncio.Lock()

    async def claim(self, stage: str) -> bool:
        if stage not in self._remaining:
            raise ValueError(f"unknown render stage: {stage}")
        async with self._lock:
            if self._remaining[stage] < 1:
                return False
            self._remaining[stage] -= 1
            self._claimed[stage] += 1
            return True

    async def snapshot(self) -> dict[str, Mapping[str, int]]:
        async with self._lock:
            return {
                "remaining": dict(self._remaining),
                "claimed": dict(self._claimed),
            }


_EXPAND_NAMES = (
    "展开",
    "阅读全文",
    "显示更多",
    "read more",
    "show more",
)
_NEXT_NAMES = ("下一页", "next")
_CRASH_MARKERS = (
    "target page, context or browser has been closed",
    "browser has been closed",
    "browser closed",
    "browser disconnected",
    "connection closed",
)


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise PlaywrightRenderError("invalid_url", retriable=False)
    return parts.scheme.casefold(), parts.hostname.casefold(), parts.port


def resolve_browser_executable(root: str | Path, *, contract=None) -> Path:
    """Resolve one complete headless-shell from an injected registry root."""
    from deskpet.playwright_bundle import get_platform_contract

    contract = contract or get_platform_contract()
    base = Path(root).resolve()
    matches = sorted(
        base.glob(
            f"chromium_headless_shell-*/{contract.executable_relative}"
        )
    )
    complete = [
        path
        for path in matches
        if (path.parents[1] / "INSTALLATION_COMPLETE").is_file()
    ]
    if len(complete) != 1:
        raise PlaywrightRenderError(
            f"browser_bundle_ambiguous:{len(complete)}", retriable=False
        )
    return complete[0]


class PlaywrightRendererPool:
    """One lazy browser process with isolated per-render contexts."""

    def __init__(
        self,
        *,
        executable_resolver: Callable[[], str | Path] | None = None,
        browser_root_resolver: Callable[[], str | Path] | None = None,
        maximum_concurrency: int = 2,
        idle_shutdown_s: float = 30.0,
        maximum_actions: int = 8,
        maximum_pages: int = 3,
        playwright_factory: Callable[[], object] | None = None,
    ) -> None:
        if (executable_resolver is None) == (browser_root_resolver is None):
            raise ValueError("inject exactly one browser executable/root resolver")
        if maximum_concurrency < 1 or maximum_concurrency > 2:
            raise ValueError("maximum_concurrency must be between 1 and 2")
        if maximum_actions < 1 or maximum_pages < 1:
            raise ValueError("action and page limits must be positive")
        self._executable_resolver = executable_resolver
        self._browser_root_resolver = browser_root_resolver
        self._maximum_actions = maximum_actions
        self._maximum_pages = maximum_pages
        self._idle_shutdown_s = max(0.0, float(idle_shutdown_s))
        self._playwright_factory = playwright_factory
        self._semaphore = asyncio.Semaphore(maximum_concurrency)
        self._lifecycle_lock = asyncio.Lock()
        self._browser = None
        self._playwright = None
        self._idle_task: asyncio.Task[None] | None = None
        self._active_contexts = 0
        self._closed = False
        self._peak_contexts = 0
        self._owned_processes: dict[int, float] = {}

    @property
    def peak_contexts(self) -> int:
        return self._peak_contexts

    @property
    def active_contexts(self) -> int:
        return self._active_contexts

    def _executable(self) -> Path:
        if self._executable_resolver is not None:
            path = Path(self._executable_resolver()).resolve()
        else:
            assert self._browser_root_resolver is not None
            path = resolve_browser_executable(self._browser_root_resolver())
        if not path.is_file():
            raise PlaywrightRenderError("browser_executable_missing", retriable=False)
        return path

    async def _ensure_browser(self):
        async with self._lifecycle_lock:
            if self._closed:
                raise PlaywrightRenderError("renderer_closed", retriable=False)
            # A previous render may have armed idle shutdown.  Cancel it while
            # holding the same lock used by shutdown, before exposing a browser
            # to the new render.  This closes the zero-active-context race
            # between returning a browser and allocating its next context.
            idle, self._idle_task = self._idle_task, None
            if idle is not None and idle is not asyncio.current_task():
                idle.cancel()
            browser = self._browser
            if browser is not None and bool(browser.is_connected()):
                return browser
            await self._close_browser_locked()
            executable = self._executable()
            processes_before = self._process_snapshot(executable)
            factory = self._playwright_factory
            if factory is None:
                from playwright.async_api import async_playwright

                manager = async_playwright()
            else:
                manager = factory()
            try:
                self._playwright = await manager.start()
                self._browser = await self._playwright.chromium.launch(
                    executable_path=str(executable),
                    headless=True,
                    args=(
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--disable-sync",
                        "--no-first-run",
                    ),
                )
            except BaseException as exc:
                self._capture_owned_processes(executable, processes_before)
                if self._playwright is not None:
                    await self._stop_playwright_locked()
                else:
                    # ``start()`` creates its driver connection before its
                    # first await.  If deadline/cancellation interrupts that
                    # await, close the partially-started context manager so a
                    # Node driver is not detached from the backend.
                    try:
                        await manager.__aexit__(type(exc), exc, exc.__traceback__)
                    except Exception:
                        pass
                raise
            self._capture_owned_processes(executable, processes_before)
            return self._browser

    @staticmethod
    def _process_snapshot(
        executable: Path,
    ) -> dict[int, tuple[float, bool, bool, int]]:
        try:
            import psutil
        except ImportError:
            return {}
        expected = str(executable).casefold()
        result: dict[int, tuple[float, bool, bool, int]] = {}
        for process in psutil.process_iter(("exe", "cmdline", "create_time", "ppid")):
            try:
                command = " ".join(process.info["cmdline"] or ()).replace("\\", "/").casefold()
                actual = str(process.info["exe"] or "").casefold()
                is_browser = actual == expected
                is_driver = "playwright/driver/package/cli.js run-driver" in command
                if is_browser or is_driver:
                    result[process.pid] = (
                        float(process.info["create_time"]),
                        is_browser,
                        is_driver,
                        int(process.info["ppid"] or 0),
                    )
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        return result

    def _capture_owned_processes(
        self,
        executable: Path,
        before: Mapping[int, tuple[float, bool, bool, int]],
    ) -> None:
        after = self._process_snapshot(executable)
        new_ids = {
            pid
            for pid, record in after.items()
            if pid not in before or before[pid][0] != record[0]
        }
        browser_ids = {pid for pid in new_ids if after[pid][1]}
        owned_ids = set(browser_ids)
        # A different DeskPet feature may start its own Playwright driver at
        # the same time.  Only claim a new driver when it is an ancestor of
        # this pool's exact pinned headless-shell process; never claim every
        # contemporaneous ``run-driver`` process globally.
        for browser_pid in browser_ids:
            parent_pid = after[browser_pid][3]
            visited: set[int] = set()
            while parent_pid and parent_pid not in visited:
                visited.add(parent_pid)
                parent = after.get(parent_pid)
                if parent is None:
                    break
                if parent_pid in new_ids and parent[2]:
                    owned_ids.add(parent_pid)
                parent_pid = parent[3]
        self._owned_processes.update(
            {pid: after[pid][0] for pid in owned_ids}
        )

    async def _reap_owned_processes(self) -> None:
        if not self._owned_processes:
            return
        try:
            import psutil
        except ImportError:
            self._owned_processes.clear()
            return

        def alive() -> list:
            values = []
            for pid, created in self._owned_processes.items():
                try:
                    process = psutil.Process(pid)
                    if abs(process.create_time() - created) < 0.001 and process.is_running():
                        values.append(process)
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue
            return values

        for _ in range(100):
            if not alive():
                self._owned_processes.clear()
                return
            await asyncio.sleep(0.02)
        remaining = alive()
        for process in remaining:
            try:
                process.terminate()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
        _, alive_after_terminate = await asyncio.to_thread(
            psutil.wait_procs, remaining, timeout=0.5
        )
        for process in alive_after_terminate:
            try:
                process.kill()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
        if alive_after_terminate:
            await asyncio.to_thread(psutil.wait_procs, alive_after_terminate, timeout=0.5)
        self._owned_processes.clear()

    async def _await_guarded(
        self,
        awaitable: Awaitable,
        *,
        deadline: float,
        cancel_event: asyncio.Event | None,
    ):
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise PlaywrightRenderError("timeout")
        task = asyncio.ensure_future(awaitable)
        cancel_task = (
            asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
        )
        try:
            waiting = {task}
            if cancel_task is not None:
                waiting.add(cancel_task)
            done, _ = await asyncio.wait(
                waiting, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                return task.result()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if cancel_task is not None and cancel_task in done:
                raise asyncio.CancelledError
            raise PlaywrightRenderError("timeout")
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)

    async def _route_read_only(self, route) -> None:
        method = str(route.request.method).upper()
        if method not in {"GET", "HEAD", "OPTIONS"}:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _assert_allowed_url(
        self, page, *, allowed_origins: set[tuple[str, str, int | None]]
    ) -> None:
        if _origin(page.url) not in allowed_origins:
            raise PlaywrightRenderError("cross_origin_navigation", retriable=False)

    async def _exact_click(self, page, names: Iterable[str], *, pagination: bool) -> bool:
        selector = (
            "nav[aria-label*='pagination' i] a, nav[aria-label*='pagination' i] button, "
            "a[rel='next']"
            if pagination
            else "a, button"
        )
        locator = page.locator(selector)
        for index in range(await locator.count()):
            item = locator.nth(index)
            name = " ".join((await item.inner_text()).split()).casefold()
            if name not in {value.casefold() for value in names}:
                continue
            if not pagination and await item.evaluate("node => Boolean(node.closest('form'))"):
                continue
            await item.click()
            return True
        return False

    async def _action(
        self,
        page,
        action: ReadOnlyAction,
        *,
        deadline: float,
        cancel_event: asyncio.Event | None,
    ) -> bool:
        if action is ReadOnlyAction.WAIT_FOR_BODY:
            await self._await_guarded(
                page.wait_for_selector("body", state="attached"),
                deadline=deadline,
                cancel_event=cancel_event,
            )
            return False
        if action is ReadOnlyAction.BOUNDED_SCROLL:
            await self._await_guarded(
                page.evaluate("window.scrollBy(0, Math.min(window.innerHeight, 900))"),
                deadline=deadline,
                cancel_event=cancel_event,
            )
            return False
        if action is ReadOnlyAction.EXPAND_READ_MORE:
            return bool(
                await self._await_guarded(
                    self._exact_click(page, _EXPAND_NAMES, pagination=False),
                    deadline=deadline,
                    cancel_event=cancel_event,
                )
            )
        if action is ReadOnlyAction.NEXT_PAGE:
            return bool(
                await self._await_guarded(
                    self._exact_click(page, _NEXT_NAMES, pagination=True),
                    deadline=deadline,
                    cancel_event=cancel_event,
                )
            )
        if action is ReadOnlyAction.GOTO:
            raise PlaywrightRenderError("duplicate_goto_action", retriable=False)
        raise PlaywrightRenderError("unknown_action", retriable=False)

    async def _capture(self, page, page_index: int) -> tuple[str, RenderPagePart]:
        html = await page.content()
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        page_part_id = hashlib.sha256(
            f"{page_index}:{page.url}:{digest}".encode("utf-8")
        ).hexdigest()[:24]
        return html, RenderPagePart(page_part_id, page_index, page.url, digest)

    @staticmethod
    def _merge_html(parts: Sequence[str]) -> str:
        if len(parts) == 1:
            return parts[0]
        seen: set[str] = set()
        bodies: list[str] = []
        for html in parts:
            match = re.search(r"<body[^>]*>(.*?)</body>", html, re.I | re.S)
            body = match.group(1) if match else html
            paragraphs = re.findall(r"<(?:p|article|section)[^>]*>.*?</(?:p|article|section)>", body, re.I | re.S)
            values = paragraphs or [body]
            for value in values:
                digest = hashlib.sha256(re.sub(r"\s+", " ", value).strip().encode()).hexdigest()
                if digest not in seen:
                    seen.add(digest)
                    bodies.append(value)
        return "<html><body>" + "\n".join(bodies) + "</body></html>"

    async def _render_once(
        self,
        url: str,
        *,
        deadline: float,
        cancel_event: asyncio.Event | None,
        actions: tuple[ReadOnlyAction, ...],
        allowed_origins: set[tuple[str, str, int | None]],
    ) -> RenderOutcome:
        browser = await self._await_guarded(
            self._ensure_browser(), deadline=deadline, cancel_event=cancel_event
        )
        context = page = None
        self._active_contexts += 1
        self._peak_contexts = max(self._peak_contexts, self._active_contexts)
        try:
            context = await self._await_guarded(
                browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                    java_script_enabled=True,
                ),
                deadline=deadline,
                cancel_event=cancel_event,
            )
            page = await self._await_guarded(
                context.new_page(), deadline=deadline, cancel_event=cancel_event
            )
            navigation_violations: list[str] = []

            async def route_read_only(route) -> None:
                request = route.request
                if request.is_navigation_request():
                    try:
                        allowed = _origin(request.url) in allowed_origins
                    except PlaywrightRenderError:
                        allowed = False
                    if not allowed:
                        navigation_violations.append(str(request.url))
                        await route.abort("blockedbyclient")
                        return
                await self._route_read_only(route)

            await page.route("**/*", route_read_only)
            try:
                await self._await_guarded(
                    page.goto(url, wait_until="domcontentloaded"),
                    deadline=deadline,
                    cancel_event=cancel_event,
                )
            except BaseException as exc:
                if navigation_violations:
                    raise PlaywrightRenderError(
                        "cross_origin_navigation", retriable=False
                    ) from exc
                raise
            await self._assert_allowed_url(page, allowed_origins=allowed_origins)
            html_parts: list[str] = []
            page_parts: list[RenderPagePart] = []
            html, part = await self._capture(page, 0)
            html_parts.append(html)
            page_parts.append(part)
            for action in actions:
                await self._action(
                    page, action, deadline=deadline, cancel_event=cancel_event
                )
                await asyncio.sleep(0)
                if navigation_violations:
                    raise PlaywrightRenderError(
                        "cross_origin_navigation", retriable=False
                    )
                # Waiting is cheap when the current document is already ready,
                # and closes the race where a delayed client-side redirect has
                # started but ``page.url``/``page.content`` still describe the
                # previous document.
                await self._await_guarded(
                    page.wait_for_load_state("domcontentloaded"),
                    deadline=deadline,
                    cancel_event=cancel_event,
                )
                # A delayed client-side redirect can occur during wait/scroll
                # without the action helper reporting a navigation.  Enforce
                # the domain fence after every action, before capturing HTML.
                await self._assert_allowed_url(page, allowed_origins=allowed_origins)
                try:
                    html, part = await self._capture(page, len(page_parts))
                except BaseException as exc:
                    if navigation_violations:
                        raise PlaywrightRenderError(
                            "cross_origin_navigation", retriable=False
                        ) from exc
                    raise
                if part.content_hash != page_parts[-1].content_hash:
                    if len(page_parts) >= self._maximum_pages:
                        raise PlaywrightRenderError("page_limit_exceeded", retriable=False)
                    html_parts.append(html)
                    page_parts.append(part)
            return RenderOutcome(
                html=self._merge_html(html_parts),
                final_url=page.url,
                page_parts=tuple(page_parts),
            )
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            if context is not None:
                try:
                    await context.clear_cookies()
                    await context.close()
                except Exception:
                    pass
            self._active_contexts -= 1
            await self._schedule_idle_shutdown()

    def _is_crash(self, exc: BaseException) -> bool:
        if isinstance(exc, asyncio.CancelledError):
            return False
        text = str(exc).casefold()
        browser = self._browser
        disconnected = browser is not None and not bool(browser.is_connected())
        return disconnected or any(marker in text for marker in _CRASH_MARKERS)

    async def render(
        self,
        url: str,
        *,
        timeout: float = 10.0,
        deadline: float | None = None,
        cancel_event: asyncio.Event | None = None,
        actions: Iterable[ReadOnlyAction | str] = (ReadOnlyAction.WAIT_FOR_BODY,),
        allowed_origins: Iterable[str] = (),
    ) -> RenderOutcome:
        if timeout <= 0:
            raise PlaywrightRenderError("timeout")
        action_values = tuple(ReadOnlyAction(value) for value in actions)
        if len(action_values) > self._maximum_actions:
            raise PlaywrightRenderError("action_limit_exceeded", retriable=False)
        origins = {_origin(url), *(_origin(value) for value in allowed_origins)}
        hard_deadline = min(
            time.monotonic() + timeout,
            deadline if deadline is not None else float("inf"),
        )
        restarted = False
        async with self._semaphore:
            for attempt in range(2):
                try:
                    result = await self._render_once(
                        url,
                        deadline=hard_deadline,
                        cancel_event=cancel_event,
                        actions=action_values,
                        allowed_origins=origins,
                    )
                    if restarted:
                        return RenderOutcome(
                            result.html,
                            result.final_url,
                            result.page_parts,
                            True,
                        )
                    return result
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:
                    if attempt == 0 and self._is_crash(exc):
                        restarted = True
                        await self._invalidate_browser()
                        continue
                    if isinstance(exc, PlaywrightRenderError):
                        raise
                    raise PlaywrightRenderError("render_failed") from exc
        raise PlaywrightRenderError("render_failed")

    async def _schedule_idle_shutdown(self) -> None:
        async with self._lifecycle_lock:
            if self._closed or self._active_contexts:
                return
            if self._idle_task is not None:
                self._idle_task.cancel()
            self._idle_task = asyncio.create_task(self._idle_shutdown())

    async def _idle_shutdown(self) -> None:
        try:
            await asyncio.sleep(self._idle_shutdown_s)
            async with self._lifecycle_lock:
                if not self._active_contexts:
                    await self._close_browser_locked()
        except asyncio.CancelledError:
            return

    async def _stop_playwright_locked(self) -> None:
        manager, self._playwright = self._playwright, None
        if manager is not None:
            try:
                await manager.stop()
            except Exception:
                pass

    async def _close_browser_locked(self) -> None:
        browser, self._browser = self._browser, None
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        await self._stop_playwright_locked()
        await self._reap_owned_processes()

    async def _invalidate_browser(self) -> None:
        async with self._lifecycle_lock:
            await self._close_browser_locked()

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            idle, self._idle_task = self._idle_task, None
            if idle is not None and idle is not asyncio.current_task():
                idle.cancel()
            await self._close_browser_locked()
        if idle is not None and idle is not asyncio.current_task():
            await asyncio.gather(idle, return_exceptions=True)

    shutdown = close


__all__ = [
    "FetchRenderBudget",
    "PlaywrightRenderError",
    "PlaywrightRendererPool",
    "ReadOnlyAction",
    "RenderOutcome",
    "RenderPagePart",
    "resolve_browser_executable",
]
