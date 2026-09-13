# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Bounded document-conflict inputs for the existing isolated document UI gate.

Only Provider responses are controlled. Source registration, graph commit, Worker
acceptance, conflict creation, verification, approval and ruling belong to the
current SDK. This fixture says nothing about a real model's reasoning quality.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CASE = "n4-document-contextual-arbitration"
KEY = "world.offline"
SOURCES = {
    "sources/indoor.md": "资料甲：方案仅在室内环境支持离线运行。\n",
    "sources/outdoor.md": "资料乙：方案在室外环境不支持离线运行。\n",
}
OUTPUTS = {"sources/indoor.md": "indoor.md", "sources/outdoor.md": "outdoor.md"}
# A model's candidate world statement is distinct from the quoted source text.
# Exact quote == content is a source-attribution claim under doc7, which correctly
# receives a source-scoped attribution key instead of this shared world key.
CONCLUSIONS = {
    "sources/indoor.md": "该方案在室内环境具备离线运行能力。",
    "sources/outdoor.md": "该方案在室外环境不具备离线运行能力。",
}
REPORT = "arbitration/world.offline/report.md"
DOSSIER = (
    "# 双方来源的适用范围\n\n"
    + SOURCES["sources/indoor.md"]
    + SOURCES["sources/outdoor.md"]
    + "两份来源对同一离线能力给出相反 stance；各有环境限定，交由人工裁决。\n"
)


def document_arbitration_materials() -> tuple[dict[str, str], ...]:
    """Public UI import batch; no filesystem or Store write."""
    return tuple(
        {"path": path, "content": content, "kind": "text/markdown"}
        for path, content in SOURCES.items()
    )


def document_arbitration_mission() -> dict[str, Any]:
    """Exact UI Mission input; reserve is required for a real Conflict Task."""
    return {
        "goal": "比较室内与室外离线能力记载，保留双方原句并把正式冲突交给人工仲裁。",
        "success_criteria": ["cite:sources/outdoor.md"],
        "domain": "doc-research-v1",
        "idempotency_key": "native-document-contextual-arbitration",
        "conflict_reserve_tokens": 30_000,
        "budget": {"max_tokens": 240_000, "max_attempts": 6},
    }


def _package(request: Any) -> dict[str, Any]:
    from agent_orchestrator.testing.fixtures import package_of

    value = package_of(request)
    domain = value.get("domain") or {}
    if domain.get("id") != "doc-research-v1" or domain.get("version") not in {"7", "8", "9"}:
        raise ValueError("arbitration fixture requires registered doc7, doc8 or doc9")
    expected = {path: hashlib.sha256(text.encode()).hexdigest() for path, text in SOURCES.items()}
    if value.get("source_versions") != expected:
        raise ValueError("arbitration fixture requires both exact registered source versions")
    return value


def _values(request: Any) -> list[dict[str, Any]]:
    values = []
    for message in request.messages:
        if str(message.role) == "tool":
            body = json.loads(message.content)
            value = body.get("value")
            if not isinstance(value, dict):
                raise ValueError("workspace tool yielded no value")
            values.append(value)
    return values


def _read(request: Any, path: str, expected: str) -> tuple[str, dict[str, Any]] | None:
    pages = [v for v in _values(request) if v.get("path") == path and "content" in v]
    if not pages:
        return "workspace_read_file", {"path": path}
    version = hashlib.sha256(expected.encode()).hexdigest()
    offset = 0
    for page in pages:
        if page.get("sha256", version) != version or page.get("offset", offset) != offset:
            raise ValueError(f"changed or discontinuous read: {path}")
        offset += len(page["content"])
    continuation = pages[-1].get("next_offset")
    if continuation is not None:
        if continuation != offset or continuation <= 0:
            raise ValueError(f"invalid continuation: {path}")
        return "workspace_read_file", {
            "path": path, "offset": continuation, "expected_sha256": version,
        }
    if "".join(page["content"] for page in pages) != expected:
        raise ValueError(f"actual bytes differ from fixture: {path}")
    return None


def document_arbitration_provider(root: Path) -> Any:
    """Provider factory for the existing document-ui isolation gate."""
    from agent_orchestrator.testing.fixtures import (
        RoleScriptedProvider,
        envelope_step,
        graph_proposal_step,
    )

    root = Path(root).resolve(strict=True)
    if not root.is_dir() or ".local-test-evidence" not in root.parts:
        raise ValueError("arbitration fixture root must be ignored local test evidence")
    versions = {path: hashlib.sha256(text.encode()).hexdigest() for path, text in SOURCES.items()}

    def planner(request: Any) -> str:
        value = _package(request)
        mission = document_arbitration_mission()
        if value["mission"]["success_criteria"] != mission["success_criteria"]:
            raise ValueError("Mission criteria changed")
        pool = value["budget_for_tasks"]["max_tokens"]
        return graph_proposal_step([
            {
                "key": key,
                "goal": f"依据 {path} 的完整原句写 {OUTPUTS[path]}，保留适用环境。",
                "rationale": "两位独立 Worker 分别呈交带来源的相反结论。",
                "dependencies": [] if key == "indoor" else ["indoor"],
                "success_criteria": ["cite:" + path],
                "verification_policy": ["format_check", "rule_check", "critic_review"],
                "outputs": [OUTPUTS[path]],
                "allowed_tools": ["workspace_read_file", "workspace_write_file", "workspace_list"],
                "budget": {"max_tokens": pool // 2 if key == "indoor" else pool - pool // 2,
                           "max_attempts": 2},
            }
            for key, path in (("indoor", "sources/indoor.md"), ("outdoor", "sources/outdoor.md"))
        ])

    def worker(request: Any) -> Any:
        value = _package(request)
        outputs = value["task_contract"]["outputs"]
        [output] = outputs
        [path] = [source for source, target in OUTPUTS.items() if target == output]
        content = SOURCES[path]
        pending = _read(request, path, content)
        if pending is not None:
            return pending
        if not any(v.get("path") == output for v in _values(request)):
            return "workspace_write_file", {"path": output, "content": content}
        [criterion] = value["doc_assessment"]["criteria"]
        if criterion["text"] != "cite:" + path:
            raise ValueError("Worker criterion changed")
        quote = content.strip()
        conclusion = CONCLUSIONS[path]
        def cited(body: dict[str, Any]) -> dict[str, Any]:
            body["claims"] = [{
                "content": conclusion, "confidence": 0.8, "key": KEY,
                "stance": "affirms" if path.endswith("indoor.md") else "refutes",
                "criterion_ids": [criterion["criterion_id"]],
                "citations": [{"path": path, "version": versions[path],
                               "start_line": 1, "end_line": 1, "quote": quote}],
            }]
            return body
        return envelope_step(summary="来源限定候选", artifacts=[output],
                             claims=[conclusion], override=cited)(request)

    def arbiter(request: Any) -> Any:
        value = _package(request)
        dispute = value.get("dispute") or {}
        if value["task_contract"]["kind"] != "conflict" or dispute.get("key") != KEY:
            raise ValueError("Arbiter was not called for the formal conflict")
        if len(dispute.get("claim_ids") or []) != 2:
            raise ValueError("Arbiter did not receive both conflict members")
        for path, expected in (
            *SOURCES.items(),
            *((output, SOURCES[path]) for path, output in OUTPUTS.items()),
        ):
            pending = _read(request, path, expected)
            if pending is not None:
                return pending
        if not any(v.get("path") == REPORT for v in _values(request)):
            return "workspace_write_file", {"path": REPORT, "content": DOSSIER}
        def cited(body: dict[str, Any]) -> dict[str, Any]:
            body["claims"] = [{
                "content": "双方环境限定不同，需人工裁决。", "confidence": 0.8,
                "key": KEY,
                "citations": [
                    {"path": path, "version": versions[path], "start_line": 1,
                     "end_line": 1, "quote": content.strip()}
                    for path, content in SOURCES.items()
                ],
            }]
            return body
        return envelope_step(summary="已读取两来源及两 Worker 产物，提交人工裁决",
                             artifacts=[REPORT], claims=["双方环境限定不同，需人工裁决。"],
                             override=cited)(request)

    def critic(request: Any) -> Any:
        value = _package(request)
        contract = value["task_contract"]
        if contract["kind"] == "conflict":
            output, expected = REPORT, DOSSIER
        else:
            output = contract["outputs"][0]
            expected = next(SOURCES[path] for path, target in OUTPUTS.items() if target == output)
        pending = _read(request, output, expected)
        if pending is not None:
            return pending
        body = {"verdict": "PASS", "findings": [], "mission_criteria": [
            {"criterion": criterion, "met": True,
             "reason": "已通过 workspace_read_file 核对实际产物原文"}
            for criterion in value["mission_success_criteria"]
        ]}
        return "<critic_verdict>" + json.dumps(body, ensure_ascii=False) + "</critic_verdict>"

    return RoleScriptedProvider({
        "planner": [planner] * 8, "worker": [worker] * 24,
        "arbiter": [arbiter] * 24, "critic": [critic] * 24,
    })
