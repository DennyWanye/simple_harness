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
    """试用前 2026-10-08：命令行里的密钥不交给模型；普通参数不误伤；隐藏两次结果不变。

    **改坏检验**：``process_list`` 直接返回原始 ``cmdline`` → 下一条用例红；名字按子串匹配 →
    这条第二段（``--max-tokens`` 等）红。"""
    from deskpet.tools.os_tools.process_tools import _redacted_argv

    argv = ["python", "serve.py", "--api-key=abc123", "--token", "t0ps3cret", "DEEPSEEKER_APIKEY=xyz",
            "--password", "p@ss", "--model", "flash", "sk-" + "a" * 24, "Bearer " + "b" * 20, "--verbose",
            "--key", "k1", "FOO_KEY=k2", "Authorization: token tk", "https://user:pw@example.com/x",
            "curl --token inner -H 'X-Api-Key: hv'"]
    assert _redacted_argv(argv) == [
        "python", "serve.py", "--api-key=[REDACTED]", "--token", "[REDACTED]", "DEEPSEEKER_APIKEY=[REDACTED]",
        "--password", "[REDACTED]", "--model", "flash", "[REDACTED]", "Bearer [REDACTED]", "--verbose",
        "--key", "[REDACTED]", "FOO_KEY=[REDACTED]", "Authorization: token [REDACTED]",
        "https://user:[REDACTED]@example.com/x", "curl --token [REDACTED] -H 'X-Api-Key: [REDACTED]'"]
    assert _redacted_argv(_redacted_argv(argv)) == _redacted_argv(argv)
    plain = ["llm", "--max-tokens", "4096", "--tokenizer", "/m/tok", "--author", "bob", "--monkey", "x"]
    assert _redacted_argv(plain) == plain


@pytest.mark.asyncio
async def test_a_process_listed_with_hidden_secrets_can_still_be_stopped():
    """核验阻断项（2026-10-08）：模型停外部进程只能交回列表给它的那份命令行（已隐藏密钥），
    身份比对两边按同一规则隐藏后再比，照样停得掉；进程号与启动时间仍要对上。

    **改坏检验**：``_matches_identity`` 只比原始命令行 → 停进程报身份不符，变红。"""
    import subprocess
    import sys

    from deskpet.tools.os_tools.process_tools import process_stop

    marker = "proc-redact-stop-check"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", marker, "--api-key=abc123"])
    try:
        await asyncio.sleep(0.3)
        listed = json.loads(await process_list({"query": marker}))
        [row] = [p for p in listed["processes"] if p["pid"] == child.pid]
        assert "--api-key=[REDACTED]" in row["command_line"] and "abc123" not in json.dumps(row)
        wrong_time = json.loads(await process_stop({"pid": row["pid"], "creation_time": row["creation_time"] + 5,
                                                    "command_line": row["command_line"]}))
        assert wrong_time.get("ok") is False and child.poll() is None, wrong_time  # 启动时间不对：不停
        stopped = json.loads(await process_stop({"pid": row["pid"], "creation_time": row["creation_time"],
                                                 "command_line": row["command_line"]}))
        assert stopped.get("ok") is True and child.pid in stopped["cleanup"]["stopped_pids"], stopped
        assert child.wait(timeout=10) is not None
    finally:
        if child.poll() is None:
            child.kill()


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
