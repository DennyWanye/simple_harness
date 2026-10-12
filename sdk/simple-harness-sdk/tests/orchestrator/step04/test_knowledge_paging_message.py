"""2026-10-09 架构挑战第 8 条（第二批）：知识列表翻页被拒时说清要带什么。真机第 3 局第 4 步的审阅员
连翻 4 次被拒、撞上调用上限，因为被拒信息只说"目录变了，重新翻"，没说要带第一页返回的 sha256。
只改返回内容，不改工具说明（工具说明进执行池准入身份）。"""
from __future__ import annotations

import pytest
from agent_orchestrator.context.knowledge_tools import read_knowledge_tool
from agent_orchestrator.storage.store import Store


def test_a_page_request_without_the_digest_is_told_what_to_send(tmp_path):
    store = Store.open(tmp_path / "s.db")
    first = read_knowledge_tool(store, "mission-x", "knowledge_list", {"offset": 0, "limit": 5})
    digest = first["sha256"]
    with pytest.raises(ValueError) as refused:
        read_knowledge_tool(store, "mission-x", "knowledge_list", {"offset": 1, "limit": 5})
    assert "needs expected_sha256" in str(refused.value) and digest in str(refused.value)
    with pytest.raises(ValueError) as changed:
        read_knowledge_tool(store, "mission-x", "knowledge_list", {"offset": 1, "limit": 5, "expected_sha256": "0" * 64})
    assert "restart pagination from offset 0" in str(changed.value) and digest in str(changed.value)
