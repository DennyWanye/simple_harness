from __future__ import annotations

import json
import importlib.metadata
from pathlib import Path

import agent_reach.channels

from deskpet.tools.agent_reach_port import AgentReachPort, _safe_message
from deskpet.tools.agent_reach_tools import _doctor, _read


class _Channel:
    def __init__(
        self,
        name: str,
        *,
        handles: bool = False,
        text: str | None = None,
        active_backend: str | None = None,
    ) -> None:
        self.name = name
        self.description = f"{name} channel"
        self.tier = 1
        self.backends = ["fixture"]
        self.active_backend = active_backend
        self._handles = handles
        self._text = text

    def can_handle(self, url: str) -> bool:
        return self._handles

    def check(self, config):
        del config
        return "ok", "ready"

    def read(self, url: str) -> str:
        del url
        if self._text is None:
            raise AssertionError("read should not be called")
        return self._text


def test_doctor_returns_only_requested_channels(monkeypatch) -> None:
    channels = [_Channel("github"), _Channel("web", active_backend="Jina Reader")]
    monkeypatch.setattr("agent_reach.channels.get_all_channels", lambda: channels)

    result = AgentReachPort().doctor(names=["web"])

    assert list(result) == ["web"]
    assert result["web"]["active_backend"] == "Jina Reader"
    json.dumps(result)


def test_pinned_agent_reach_channel_contract() -> None:
    distribution = importlib.metadata.distribution("agent-reach")
    direct_url = json.loads(
        (Path(distribution._path) / "direct_url.json").read_text(encoding="utf-8")
    )
    assert direct_url["url"].endswith(
        "/e825f6740d24c6c315c3b0dc41907e6c87ff39a5.zip"
    )
    lock = (Path(__file__).parents[1] / "uv.lock").read_text(encoding="utf-8")
    assert (
        'hash = "sha256:bdceee5847d2f20744621c398cf38597abf7709920f4be697348c0e0c708e9d6"'
        in lock
    )
    channels = agent_reach.channels.get_all_channels()
    names = [channel.name for channel in channels]

    assert names
    assert len(names) == len(set(names))
    assert {"github", "web", "youtube", "rss"}.issubset(names)
    assert all(callable(channel.can_handle) for channel in channels)
    assert all(callable(channel.check) for channel in channels)
    web = agent_reach.channels.get_channel("web")
    assert web is not None
    assert callable(web.read)
    github = agent_reach.channels.get_channel("github")
    assert github.can_handle("https://github.com/Panniantong/Agent-Reach")
    status, message = web.check()
    assert status == "ok"
    assert isinstance(message, str) and message


def test_platform_without_read_uses_agent_reach_web_backend(monkeypatch) -> None:
    class GithubChannel:
        name = "github"
        description = "github channel"
        tier = 1
        backends = ["gh CLI"]
        active_backend = None

        def can_handle(self, url: str) -> bool:
            return True

        def check(self, config):
            del config
            return "warn", "gh missing"

    github = GithubChannel()

    class WebChannel(_Channel):
        def read(self, url: str) -> str:
            return "Title: Agent-Reach\n\nDirect upstream content " * 20

    web = WebChannel("web", active_backend="Jina Reader")
    monkeypatch.setattr(
        "agent_reach.channels.get_all_channels", lambda: [github, web]
    )
    monkeypatch.setattr(
        "agent_reach.channels.get_channel",
        lambda name: web if name == "web" else None,
    )

    evidence = AgentReachPort().read_url("https://github.com/example/repo")

    assert evidence.ok is True
    assert evidence.channel == "github"
    assert evidence.active_backend == "Jina Reader"
    assert evidence.title == "Agent-Reach"
    assert "Direct upstream content" in evidence.text


def test_tool_surface_returns_normalized_json(monkeypatch) -> None:
    port = AgentReachPort()
    monkeypatch.setattr(
        "deskpet.tools.agent_reach_tools.agent_reach_port",
        port,
    )
    monkeypatch.setattr(
        port,
        "doctor",
        lambda names=None: {"web": {"status": "ok", "active_backend": "Jina Reader"}},
    )

    payload = json.loads(_doctor({"channels": ["web"]}, "task"))

    assert payload["provider"] == "agent-reach"
    assert payload["channels"]["web"]["status"] == "ok"


def test_read_tool_rejects_relative_url() -> None:
    payload = json.loads(_read({"url": "/relative"}, "task"))

    assert payload["ok"] is False
    assert payload["reason_code"] == "invalid_url"


def test_read_rejects_non_public_targets() -> None:
    port = AgentReachPort()

    for url in (
        "http://localhost:8100/private",
        "http://127.0.0.1/private",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/private",
        "https://user:pass@example.com/private",
    ):
        evidence = port.read_url(url)
        assert evidence.ok is False
        assert evidence.reason_code == "invalid_url"


def test_doctor_isolates_broken_metadata_and_bounds_backends(monkeypatch) -> None:
    good = _Channel("web", active_backend="Jina Reader")
    good.backends = [f"backend-{index}" for index in range(20)]

    class Broken:
        name = "broken"

        @property
        def description(self):
            raise RuntimeError("bad metadata")

        def check(self, config):
            del config
            return "ok", "should be isolated"

    monkeypatch.setattr(
        "agent_reach.channels.get_all_channels", lambda: [Broken(), good]
    )

    result = AgentReachPort().doctor()

    assert result["broken"]["status"] == "error"
    assert result["web"]["status"] == "ok"
    assert len(result["web"]["backends"]) == 8


def test_diagnostics_redact_common_secret_shapes() -> None:
    value = (
        'Authorization: Bearer abcdefghijklmnopqrstuvwxyz '
        '\"api_key\":\"secret-value\" '
        'https://example.com/?token=query-secret'
    )

    redacted = _safe_message(value)

    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "secret-value" not in redacted
    assert "query-secret" not in redacted
