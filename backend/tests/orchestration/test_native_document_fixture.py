# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Fixture preparation is SDK software evidence, separate from actual native clicks."""
import asyncio
from pathlib import Path

import pytest
from deskpet.orchestration.native_fixture import document_ui_provider
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings
from deskpet.orchestration.settings import resolve_test_scenario


def inputs(root):
    (root / "sources").mkdir(parents=True)
    (root / "controlled-only").mkdir()
    records = "# 合成记录\n\n" + "\n\n".join(
        f"记录{i:02}：样本仅在环境{i}完成检查，不代表其他环境已通过。" for i in range(1, 27)
    ) + "\n"
    table = "# 合成表\r\n\r\n| 编号 | 记录 |\r\n| --- | --- |\r\n" + "".join(
        f"| {i:05} | 合成样本{i}只用于完整表格分页验证，中文和emoji😀不代表现实结论。 |\r\n"
        for i in range(1, 5001)
    ) + "| 终点 | END_OF_LONG_TABLE：全文读取已到最终行。 |\r\n"
    (root / "sources/records-26.md").write_bytes(records.encode())
    (root / "controlled-only/long-table-crlf.md").write_bytes(table.encode())
    return records, table


@pytest.mark.asyncio
async def test_controlled_native_document_fixture_uses_real_verification_and_full_citation(tmp_path, principal, monkeypatch):
    root = tmp_path / ".local-test-evidence" / "inputs"
    records, table = inputs(root)
    monkeypatch.setenv("DESKPET_ORCH_UI_FIXTURE_DIR", str(root))
    service = OrchestrationService(tmp_path / "library", OrchestrationSettings(),
                                   principal=principal, test_scenario="document-ui", drive=False)
    await service.start()
    try:
        assert service.status()["available"]
        created = service.create_mission_with_sources({"mission": {
            "goal": "验证合成资料的完整来源归属", "idempotency_key": "native-doc-fixture",
            "domain": "doc-research-v1",
            "success_criteria": ["file:REPORT.md", "cite:sources/records.md", "cite:sources/long-table.md"],
        }, "sources": [
            {"path": "sources/records.md", "content": records, "kind": "text/markdown"},
            {"path": "sources/long-table.md", "content": table, "kind": "text/markdown"},
        ]})
        await asyncio.wait_for(service.drain(), timeout=30)
        detail = service.mission_detail(created["mission_id"])
        assert detail["mission"]["status"] == "COMPLETED", detail["mission"]
        claims = detail["document"]["claims"]
        assert len(claims) == 28
        assert all(c["source_trust"] == "untrusted_external" for c in claims)
        citation = next(c["citations"][0] for c in claims if c["citations"][0]["path"] == "sources/long-table.md")
        body = {key: citation[key] for key in ("mission_id", "result_id", "receipt_id", "citation_index")}
        chunks, offset = [], 0
        while True:
            page = service.citation_read({**body, "offset": offset, "limit": 32768})
            chunks.append(page["text"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        full = "".join(chunks)
        assert len(full.encode()) > 256 * 1024
        assert "END_OF_LONG_TABLE" in full and "😀" in full and "\r\n" in full
        assert detail["usage"]["reserved_tokens"] == 0
    finally:
        await service.close()


def test_native_document_fixture_requires_ignored_directory_gate(tmp_path):
    plain = Path(tmp_path.anchor) / "ordinary-fixture-oracle"
    assert resolve_test_scenario({"DESKPET_ORCHESTRATION_TEST_SCENARIO": "document-ui"}, plain) is None
    with pytest.raises((ValueError, FileNotFoundError)):
        document_ui_provider(plain)
