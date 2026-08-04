from __future__ import annotations

import hashlib
import http.server
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
from importlib import metadata

import psutil


HTML = b"""<!doctype html><html><body><main id='app'>shell</main>
<script>setTimeout(() => { document.querySelector('#app').textContent =
'DeskPet bundled Chromium dynamic fixture'; }, 50);</script></body></html>"""


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(HTML)))
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class DenyProxyHandler(http.server.BaseHTTPRequestHandler):
    blocked_requests: list[dict[str, str]] = []
    lock = threading.Lock()

    def _deny(self) -> None:
        with self.lock:
            self.blocked_requests.append(
                {"method": self.command, "target": self.path[:256]}
            )
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_CONNECT = _deny  # type: ignore[assignment]  # stdlib callback names
    do_GET = _deny  # type: ignore[assignment]
    do_HEAD = _deny  # type: ignore[assignment]
    do_POST = _deny  # type: ignore[assignment]

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _browser_root() -> pathlib.Path:
    explicit = os.environ.get("DESKPET_SPIKE_BROWSER_ROOT")
    if explicit:
        return pathlib.Path(explicit).resolve()
    bundle_root = pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(__file__).parent))
    return (bundle_root / "playwright-browsers").resolve()


class ProcessTreeSampler:
    def __init__(self) -> None:
        self._root = psutil.Process()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.peak_rss_bytes = 0
        self.tracked: dict[int, str] = {}

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._sample()

    def _sample(self) -> None:
        try:
            processes = [self._root, *self._root.children(recursive=True)]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        rss = 0
        for process in processes:
            try:
                rss += process.memory_info().rss
                self.tracked[process.pid] = process.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        self.peak_rss_bytes = max(self.peak_rss_bytes, rss)

    def _run(self) -> None:
        while not self._stop.wait(0.02):
            self._sample()

    def survivors(self) -> list[dict[str, object]]:
        survivors: list[dict[str, object]] = []
        for pid, name in sorted(self.tracked.items()):
            if pid == self._root.pid:
                continue
            if psutil.pid_exists(pid):
                try:
                    process = psutil.Process(pid)
                    survivors.append(
                        {"pid": pid, "name": name, "status": process.status()}
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        return survivors


def _headless_shell(browser_root: pathlib.Path) -> pathlib.Path:
    matches = tuple(
        browser_root.glob(
            "chromium_headless_shell-*/chrome-headless-shell-win64/"
            "chrome-headless-shell.exe"
        )
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one bundled headless shell under {browser_root}, found {len(matches)}"
        )
    return matches[0].resolve()


def main() -> int:
    browser_root = _browser_root()
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_root)
    os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    offline = os.environ.get("DESKPET_SPIKE_OFFLINE") == "1"
    deny_server: http.server.ThreadingHTTPServer | None = None
    deny_thread: threading.Thread | None = None
    launch_options: dict[str, object] = {"headless": True}
    if offline:
        DenyProxyHandler.blocked_requests = []
        deny_server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), DenyProxyHandler
        )
        deny_thread = threading.Thread(target=deny_server.serve_forever, daemon=True)
        deny_thread.start()
        deny_url = f"http://127.0.0.1:{deny_server.server_address[1]}"
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            os.environ[name] = deny_url
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["PLAYWRIGHT_DOWNLOAD_HOST"] = deny_url
        os.environ["PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST"] = deny_url
        launch_options["proxy"] = {
            "server": deny_url,
            "bypass": "127.0.0.1,localhost",
        }

    from playwright.sync_api import sync_playwright

    started = time.perf_counter()
    sampler = ProcessTreeSampler()
    sampler.start()
    output: dict[str, object] = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "playwright_version": metadata.version("playwright"),
        "browser_root": str(browser_root),
        "fixture_url": f"http://127.0.0.1:{port}/",
        "network_mode": "loopback_deny_proxy" if offline else "default",
        "success": False,
    }
    temp_home = pathlib.Path(tempfile.mkdtemp(prefix="deskpet-pw-spike-"))
    os.environ["USERPROFILE"] = str(temp_home)
    os.environ["LOCALAPPDATA"] = str(temp_home / "LocalAppData")
    try:
        bundled_executable = _headless_shell(browser_root)
        output.update(
            executable_path=str(bundled_executable),
            executable_exists=bundled_executable.is_file(),
            executable_size=bundled_executable.stat().st_size,
            executable_sha256=_sha256(bundled_executable),
        )
        driver_started = time.perf_counter()
        with sync_playwright() as playwright:
            output["driver_start_seconds"] = round(
                time.perf_counter() - driver_started, 3
            )
            browser_started = time.perf_counter()
            browser = playwright.chromium.launch(**launch_options)
            output["browser_launch_seconds"] = round(
                time.perf_counter() - browser_started, 3
            )
            try:
                page_started = time.perf_counter()
                page = browser.new_page()
                page.goto(output["fixture_url"], wait_until="networkidle")
                page.locator("#app").wait_for(state="visible")
                rendered_text = page.locator("#app").inner_text()
                output["first_page_seconds"] = round(
                    time.perf_counter() - page_started, 3
                )
                output["rendered_text"] = rendered_text
                output["rendered_text_sha256"] = hashlib.sha256(
                    rendered_text.encode("utf-8")
                ).hexdigest()
                output["success"] = rendered_text == "DeskPet bundled Chromium dynamic fixture"
            finally:
                close_started = time.perf_counter()
                browser.close()
                output["browser_close_seconds"] = round(
                    time.perf_counter() - close_started, 3
                )
    except Exception as exc:  # spike must persist diagnostic context
        output["error_type"] = type(exc).__name__
        output["error"] = str(exc)
    finally:
        cleanup_started = time.perf_counter()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if deny_server is not None:
            deny_server.shutdown()
            deny_server.server_close()
        if deny_thread is not None:
            deny_thread.join(timeout=5)
        output["blocked_external_requests"] = list(
            DenyProxyHandler.blocked_requests if offline else []
        )
        output["blocked_external_request_count"] = len(
            output["blocked_external_requests"]
        )
        sampler.stop()
        time.sleep(0.2)
        output["peak_process_tree_rss_bytes"] = sampler.peak_rss_bytes
        output["tracked_processes"] = [
            {"pid": pid, "name": name}
            for pid, name in sorted(sampler.tracked.items())
        ]
        output["orphan_processes"] = sampler.survivors()
        output["cleanup_success"] = not output["orphan_processes"]
        shutil.rmtree(temp_home, ignore_errors=True)
        output["temp_home_removed"] = not temp_home.exists()
        output["cleanup_seconds"] = round(
            time.perf_counter() - cleanup_started, 3
        )
        output["success"] = bool(output["success"]) and bool(
            output["cleanup_success"]
        ) and bool(output["temp_home_removed"])
        output["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
