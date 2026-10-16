"""阶段 7 收窄的失败路径：工具目录以外几处由宽捕获收窄成具体异常的地方。"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from deskpet.agent.assembler.components import workspace
from providers import context_os_e2e_hooks, extra_headers


def test_workspace_dir_without_platformdirs_is_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "platformdirs", None)
    component = workspace.WorkspaceComponent.__new__(workspace.WorkspaceComponent)
    component._override = None

    assert component._resolve_dir(SimpleNamespace(config={})) is None


def test_loopback_rejects_unparseable_url():
    assert context_os_e2e_hooks._loopback("http://[::1") is False


def test_host_of_unparseable_url_is_empty():
    assert extra_headers._host_of("http://[::1") == ""


def test_args_hash_falls_back_for_mixed_key_types():
    from agent.session_activity import args_hash

    mixed = {1: "a", "b": 2}  # sort_keys 遇到混合键类型抛 TypeError

    assert args_hash(mixed) == args_hash(mixed)
    assert len(args_hash(mixed)) == 12


def test_attachment_size_counts_text_when_not_base64():
    from deskpet.agent.attachment_budget import _content_block_size
    from deskpet.sdk_adapters.context_preparation import _attachment_payload_size

    block = {"data": "héllo"}  # 含非 ASCII 字符，b64decode 抛 ValueError

    assert _content_block_size(block) == len("héllo".encode())
    assert _attachment_payload_size(block) == len("héllo".encode())
