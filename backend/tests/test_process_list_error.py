# Test to reproduce process_list RuntimeError issue
import asyncio
import json

import pytest

from deskpet.tools.os_tools.process_tools import process_list


@pytest.mark.asyncio
async def test_process_list_basic():
    """Test that process_list doesn't throw RuntimeError on basic execution."""
    result = await process_list(
        args={"query": "", "max_entries": 10},
        task_id="test_task_001",
    )

    # Should return success envelope
    payload = json.loads(result)
    assert payload["ok"] is True
    assert len(payload["processes"]) <= 10


@pytest.mark.asyncio
async def test_process_list_with_query():
    """Test that process_list works with a query filter."""
    result = await process_list(
        args={"query": "python", "max_entries": 5},
        task_id="test_task_002",
    )

    payload = json.loads(result)
    assert payload["ok"] is True
    # The query matches name, executable or command line (process_tools.py
    # searchable text), not the name alone: a zsh running a python script
    # is a legitimate hit.
    def searchable(item):
        return " ".join(
            (
                str(item.get("name") or ""),
                str(item.get("executable") or ""),
                " ".join(item.get("command_line") or []),
            )
        ).casefold()

    assert all("python" in searchable(item) for item in payload["processes"])


@pytest.mark.asyncio
async def test_process_list_invalid_args():
    """Test that process_list handles invalid arguments gracefully."""
    result = await process_list(
        args={"max_entries": "not_an_integer"},  # Invalid type
        task_id="test_task_003",
    )

    # Should return error envelope, not throw exception
    payload = json.loads(result)
    assert payload == {
        "ok": False,
        "error": {
            "code": "invalid_arguments",
            "message": "max_entries must be an integer",
        },
    }


if __name__ == "__main__":
    asyncio.run(test_process_list_basic())
    asyncio.run(test_process_list_with_query())
    asyncio.run(test_process_list_invalid_args())


def test_process_list_hides_secrets_in_command_lines():
    """试用前 2026-10-08：命令行里的密钥不交给模型（``--k=v``、``--token v``、``KEY=v``、长得像密钥的串）。

    **改坏检验**：``process_list`` 直接返回原始 ``cmdline`` → 第二条用例红。"""
    from deskpet.tools.os_tools.process_tools import _redacted_argv

    argv = ["python", "serve.py", "--api-key=abc123", "--token", "t0ps3cret", "DEEPSEEKER_APIKEY=xyz",
            "--password", "p@ss", "--model", "flash", "sk-" + "a" * 24, "Bearer " + "b" * 20, "--verbose"]
    assert _redacted_argv(argv) == [
        "python", "serve.py", "--api-key=[REDACTED]", "--token", "[REDACTED]", "DEEPSEEKER_APIKEY=[REDACTED]",
        "--password", "[REDACTED]", "--model", "flash", "[REDACTED]", "[REDACTED]", "--verbose"]


@pytest.mark.asyncio
async def test_process_list_never_returns_or_matches_a_secret_argument(monkeypatch):
    import json
    from types import SimpleNamespace

    from deskpet.tools.os_tools import process_tools

    secret = "sk-" + "z" * 30
    fake = SimpleNamespace(info={"pid": 4242, "name": "worker", "exe": "/usr/bin/worker",
                                 "cmdline": ["worker", "--token", secret, f"--key={secret}"], "create_time": 1.0})
    monkeypatch.setattr(process_tools.psutil, "process_iter", lambda attrs=None: iter([fake]))
    listed = json.loads(await process_tools.process_list({"query": "worker"}))
    assert secret not in json.dumps(listed)
    assert listed["processes"][0]["command_line"] == ["worker", "--token", "[REDACTED]", "--key=[REDACTED]"]
    probed = json.loads(await process_tools.process_list({"query": secret[:12]}))
    assert probed["processes"] == []  # 不能拿密钥片段去试探谁在用它
