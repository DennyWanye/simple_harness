"""接缝脚本共用的产品同形种子（HTN 补齐阶段 A′，2026-10-03 取代 ``_assured_fixture``）。

任务经产品那一份部署组装建出（保证通道、执行图建任务时绑定、原生执行池），主循环真跑；替身只有
脚本化的模型回复（审阅员可以按审查目的一步步回答，包括调用两个只读取证工具）。每个接缝把它要诊断
的事实写成一份报告放在 git 忽略的证据目录，最后一行打印 ``{"status": "PASS", "evidence": 路径}``。
"""
from seam_paths import EVIDENCE, SDK  # noqa: F401  (side effect: sys.path, evidence dir)

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, UTC
from typing import Any

from simple_harness import MessageRole
from _review_world import ReviewScript, reviewed_mission  # noqa: F401  (re-exported for the seams)
from agent_orchestrator.testing.scripted_replies import review_input, review_reply


def tool_values(request: Any, name: str) -> list[dict[str, Any]]:
    """Successful tool-result values of ``name`` in this actual Provider request, in order."""
    values = []
    for message in request.messages:
        if message.role is MessageRole.TOOL and message.name == name:
            payload = json.loads(message.content)
            if payload["outcome"] == "succeeded":
                values.append(payload["value"])
    return values


def tool_results(request: Any) -> list[dict[str, Any]]:
    return [json.loads(m.content) for m in request.messages if m.role is MessageRole.TOOL]


class StepReviewer(ReviewScript):
    """``steps[purpose]`` 是这一类审查一次次请求的回答：字符串（最终回答）、``(工具, 参数)``，或者
    收到实际请求后再决定的函数。用完以后按通过回答。"""

    def __init__(self, steps: dict[str, list[Any]], **options: Any) -> None:
        super().__init__(**options)
        self.steps = {purpose: list(items) for purpose, items in steps.items()}
        self.purpose_requests: dict[str, list[Any]] = {}
        accept = self._answer["unknown"]

        def reviewer(request: Any) -> Any:
            data = review_input(request)
            if data is None:
                return None
            purpose = str((data.get("package") or {}).get("purpose"))
            self.purpose_requests.setdefault(purpose, []).append(request)
            queue = self.steps.get(purpose) or []
            if not queue:
                return accept(request)
            step = queue.pop(0)
            return step(request, data) if callable(step) else step

        self._answer["unknown"] = reviewer


def cite(data: dict[str, Any], labels: list[str], *, verdict: str = "ACCEPT") -> str:
    return json.dumps({"schema_version": 4, "verdict": verdict, "assessments": [
        {"criterion_id": c, "verdict": "PASS", "evidence_ids": list(labels), "reason": "cites the read evidence",
         "limitations": []} for c in data["criterion_ids"]], "findings": []}, ensure_ascii=False)


def accept(data: dict[str, Any]) -> str:
    return review_reply(data)


def source_sha256(names: list[str]) -> dict[str, str]:
    return {name: hashlib.sha256((SDK / "src/agent_orchestrator" / name).read_bytes()).hexdigest() for name in names}


def write_report(name: str, report: dict[str, Any]) -> None:
    report["finished_at"] = datetime.now(UTC).isoformat()
    path = EVIDENCE / f"{name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps({"status": "PASS", "scope": report.get("scope", ""), "evidence": str(path)}, ensure_ascii=False))


def count(store: Any, sql: str, *params: Any) -> int:
    return int(store.connection.execute(sql, params).fetchone()[0])


def quick() -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    event_handler.WAIT_BACKOFF_MAX = 0.05


__all__ = ("EVIDENCE", "SDK", "Callable", "ReviewScript", "StepReviewer", "accept", "cite", "count", "quick",
           "reviewed_mission", "source_sha256", "tool_results", "tool_values", "write_report")
