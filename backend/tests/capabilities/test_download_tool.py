from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from deskpet.tools.os_tools.download_tools import download_file
from deskpet.tools.os_tools.download_tools import resolve_download_resources

PAYLOAD = "下载 内容".encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        body = PAYLOAD if self.path.startswith("/ok") else b"x" * 4096
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


@pytest.fixture
def download_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_download_is_bounded_hash_verified_and_atomically_published(
    tmp_path: Path, download_server: str
) -> None:
    destination = tmp_path / "下载 文件.bin"
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    result = json.loads(
        await download_file(
            {
                "url": f"{download_server}/ok?secret=hidden",
                "destination": str(destination),
                "expected_sha256": digest,
                "max_bytes": 1024,
            }
        )
    )
    assert result["ok"] is True
    assert result["sha256"] == digest
    assert result["published_atomically"] is True
    assert "secret" not in result["url"]
    assert destination.read_bytes() == PAYLOAD


@pytest.mark.asyncio
async def test_download_size_and_hash_failure_leave_no_destination(
    tmp_path: Path, download_server: str
) -> None:
    too_large = tmp_path / "too-large.bin"
    result = json.loads(
        await download_file(
            {
                "url": f"{download_server}/large",
                "destination": str(too_large),
                "max_bytes": 64,
            }
        )
    )
    assert result["error"]["code"] == "download_too_large"
    assert not too_large.exists()

    mismatch = tmp_path / "mismatch.bin"
    result = json.loads(
        await download_file(
            {
                "url": f"{download_server}/ok",
                "destination": str(mismatch),
                "expected_sha256": "0" * 64,
                "max_bytes": 1024,
            }
        )
    )
    assert result["error"]["code"] == "download_hash_mismatch"
    assert not mismatch.exists()
    assert list(tmp_path.glob("*.deskpet-download-*.tmp")) == []


@pytest.mark.asyncio
async def test_download_redirect_requires_new_origin_authorization(
    tmp_path: Path, download_server: str
) -> None:
    result = json.loads(
        await download_file(
            {
                "url": f"{download_server}/redirect",
                "destination": str(tmp_path / "redirect.bin"),
                "max_bytes": 1024,
            }
        )
    )
    assert result["error"]["code"] == "download_redirect_rejected"


@pytest.mark.asyncio
async def test_download_can_require_hash_before_network_access(
    tmp_path: Path, download_server: str
) -> None:
    result = json.loads(
        await download_file(
            {
                "url": f"{download_server}/ok",
                "destination": str(tmp_path / "required.bin"),
                "max_bytes": 1024,
                "require_hash": True,
            }
        )
    )
    assert result["error"]["code"] == "download_hash_required"
    selectors = resolve_download_resources(
        {
            "url": f"{download_server}/ok?secret=not-in-origin",
            "destination": str(tmp_path / "required.bin"),
        }
    )
    assert selectors[0]["kind"] == "network_origin"
    assert "secret" not in selectors[0]["origin"]
    assert selectors[1]["kind"] == "filesystem"
