"""Minimal frozen/offline Playwright smoke for the bundled headless shell."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from deskpet.playwright_bundle import resolve_browser_bundle


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"<!doctype html><title>DeskPet</title><main id='result'>bundle-ok</main>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def main() -> int:
    diagnostic = resolve_browser_bundle()
    os.environ.update(diagnostic.environment())
    # Process-local offline guard: both the Playwright driver and Chromium see
    # a loopback deny proxy, while the deterministic fixture bypasses it.
    deny_proxy = "http://127.0.0.1:9"
    os.environ.update(
        {
            "HTTP_PROXY": deny_proxy,
            "HTTPS_PROXY": deny_proxy,
            "PLAYWRIGHT_DOWNLOAD_HOST": deny_proxy,
            "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST": deny_proxy,
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                proxy={"server": deny_proxy, "bypass": "127.0.0.1,localhost"},
            )
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
            text = page.locator("#result").inner_text()
            browser.close()
        if text != "bundle-ok":
            raise RuntimeError(f"unexpected rendered text: {text!r}")
        print(
            json.dumps(
                {"status": "ok", "text": text, "offline_guard": True,
                 **diagnostic.public_dict(launch_status="ok")}
            )
        )
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
