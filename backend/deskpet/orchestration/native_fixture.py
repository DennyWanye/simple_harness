# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Controlled native document UI data; all SDK verification and storage stay real.

This is only selected by the existing ignored-userdata/environment double gate.
It never issues a network request or writes a verdict into the orchestrator DB.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def document_ui_provider(root: Path):
    from agent_orchestrator.testing.fixtures import (
        RoleScriptedProvider,
        critic_step,
        envelope_step,
        graph_proposal_step,
        package_of,
    )

    root = root.resolve(strict=True)
    if ".local-test-evidence" not in root.parts:
        raise ValueError("native fixture inputs must be in ignored test evidence")
    files = {
        "sources/records.md": root / "sources/records-26.md",
        "sources/long-table.md": root / "controlled-only/long-table-crlf.md",
    }
    documents = {}
    for name, path in files.items():
        if path.is_symlink() or root not in path.resolve().parents:
            raise ValueError("native fixture input escapes its directory")
        documents[name] = path.read_bytes()
    versions = {path: hashlib.sha256(data).hexdigest() for path, data in documents.items()}
    entries = []
    for path, data in documents.items():
        lines = data.decode("utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            if line.startswith(("记录", "| 00001 |", "| 终点 |")):
                entries.append({"path": path, "version": versions[path], "start_line": number,
                                "end_line": number, "quote": line})
    if len(entries) != 28 or len(documents["sources/long-table.md"]) <= 256 * 1024:
        raise ValueError("native fixture requires the original 26 records and long table")
    report = "# 合成资料的来源归属\n\n" + "\n\n".join(e["quote"] for e in entries) + "\n"

    def planner(request):
        package = package_of(request)
        if package.get("source_versions") != versions:
            raise ValueError("import the exact two fixture documents with their registered paths")
        return graph_proposal_step([{
            "key": "report", "goal": package["mission"]["goal"],
            "rationale": "受控界面数据，非真实模型能力评估", "dependencies": [],
            "success_criteria": package["mission"]["success_criteria"],
            "verification_policy": ["format_check", "rule_check", "critic_review"],
            "outputs": ["REPORT.md"],
            "allowed_tools": ["workspace_read_file", "workspace_write_file", "workspace_list"],
            "budget": {"max_tokens": package["budget_for_tasks"]["max_tokens"], "max_attempts": 2},
        }])

    def worker(request):
        if not any(str(message.role) == "tool" for message in request.messages):
            return ("workspace_write_file", {"path": "REPORT.md", "content": report})
        def citations(envelope):
            for claim, citation in zip(envelope["claims"], entries, strict=True):
                claim["citations"] = [citation]
            return envelope
        return envelope_step(summary="受控原生 UI 夹具，不代表真实模型结果", artifacts=["REPORT.md"],
                             claims=[entry["quote"] for entry in entries], override=citations)(request)

    def critic(request):
        outputs = [json.loads(message.content) for message in request.messages
                   if str(message.role) == "tool"]
        if not outputs:
            return ("workspace_read_file", {"path": "REPORT.md"})
        values = [output.get("value") or {} for output in outputs]
        if any("content" not in value for value in values):
            return critic_step(verdict="FAIL", criteria_met=False)(request)
        offset = values[-1].get("next_offset")
        if offset is not None:
            return ("workspace_read_file", {"path": "REPORT.md", "offset": offset,
                                             "expected_sha256": values[-1]["sha256"]})
        actual = "".join(value.get("content", "") for value in values)
        met = actual == report
        return critic_step(verdict="PASS" if met else "FAIL", criteria_met=met)(request)

    # Request-bound callbacks are repeatable for a second UI Mission and cold restart.
    return RoleScriptedProvider({"planner": [planner] * 16, "worker": [worker] * 32,
                                 "critic": [critic] * 100})
