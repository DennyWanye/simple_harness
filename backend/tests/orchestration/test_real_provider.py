# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-11: one Mission through the Host service on a real model (opt-in, deepseek-flash only).

Run only with ``-m real_provider`` and ``SH_BASEURL`` / ``SH_APIKEY`` / ``SH_MODEL`` in the
environment (the runner sources them; nothing here reads a credentials file).  The goal is
text only and the criteria are ``file:`` plus natural language — no ``pytest:`` and no
``action:`` (plan §3.5).  Pass = the Mission reaches a terminal state: COMPLETED, or FAILED
with the stop reason recorded as it is.  With ``SH_EVIDENCE_DIR`` set, a summary and a copy
of the orchestration directory land there for the secret scan (acceptance HA-11).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from deskpet.orchestration.provider import ProviderSnapshot, build_provider, requested_model
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

pytestmark = pytest.mark.real_provider

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _snapshot() -> ProviderSnapshot:
    base_url = os.environ.get("SH_BASEURL", "")
    api_key = os.environ.get("SH_APIKEY", "")
    model = os.environ.get("SH_MODEL", "")
    if not (base_url and api_key and model):
        pytest.skip("SH_BASEURL / SH_APIKEY / SH_MODEL not set")
    assert "pro" not in model, "real runs use deepseek-flash only"
    return ProviderSnapshot(
        provider_id="deepseek-official",
        base_url=base_url,
        configured_model=model,
        requested_model=requested_model(base_url, model),
        api_key=api_key,
    )


@pytest.mark.asyncio
async def test_real_model_mission_reaches_a_terminal_state(orchestration_root, principal):
    snapshot = _snapshot()
    provider, client = build_provider(snapshot)
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        principal=principal,
        provider=provider,
        provider_snapshot=snapshot,
        http_client=client,
    )
    await service.start()
    started = time.monotonic()
    summary: dict[str, object] = {"model": snapshot.public()}
    try:
        assert service.status()["state"] == "available", service.status()
        receipt = service.create_mission(
            {
                "goal": "写一份 SUMMARY.md，用三到五句中文介绍桌面工作台里「任务编排」视图能做什么",
                "success_criteria": [
                    "file:SUMMARY.md",
                    "SUMMARY.md 是中文，三到五句，说明了新建 Mission、查看进度和人工审批",
                ],
                "idempotency_key": "ha-11-real-1",
                "budget": {"max_tokens": 400_000, "max_attempts": 3},
            }
        )
        mission_id = receipt["mission_id"]
        deadline = started + float(os.environ.get("SH_REAL_TIMEOUT", "1200"))
        detail = service.mission_detail(mission_id)
        # A Planner may put human_review into a Task's policy (run 1, 2026-09-12).  Only with
        # SH_REAL_REVIEW=pass does the test operator pass *review* requests, recorded as a
        # pre-set operator decision; the artifacts stay in the evidence for the operator to
        # check afterwards.  Any other request (action, arbitration) stops the run.
        operator_review = os.environ.get("SH_REAL_REVIEW", "")
        decisions: list[dict[str, object]] = []
        summary["operator_decisions"] = decisions
        while detail["mission"]["status"] not in TERMINAL and time.monotonic() < deadline:
            pending = [a for a in detail["approvals"] if a.get("state") == "PENDING"]
            reviews = [a for a in pending if a.get("kind") == "review"]
            if pending and (operator_review != "pass" or len(reviews) != len(pending)):
                summary["waiting_on_person"] = pending
                break
            for request in reviews:
                service.decide(
                    request["request_id"],
                    "review_pass",
                    note="HA-11 测试运行：复核按预先设置（SH_REAL_REVIEW=pass）由测试执行者通过，产物保存在证据里供事后核对",
                )
                decisions.append({"request_id": request["request_id"], "summary": request.get("summary")})
            await asyncio.sleep(5)
            detail = service.mission_detail(mission_id)

        artifacts = []
        for artifact in detail["artifacts"]:
            try:
                artifacts.append(service.artifact_read(artifact["id"]))
            except Exception as error:  # noqa: BLE001 - recorded as it is
                artifacts.append({"id": artifact.get("id"), "read_error": str(error)})
        summary.update(
            elapsed_s=round(time.monotonic() - started, 1),
            mission=detail["mission"],
            attempts=detail["attempts"],
            results=detail["results"],
            blocked=detail["blocked"],
            waiting_on=detail["waiting_on"],
            usage=detail["usage"],
            event_count=detail["event_count"],
            last_events=[e["type"] for e in service.events(mission_id, limit=200)["events"]][-12:],
            artifacts=artifacts,
        )
    finally:
        await service.close()
        evidence = os.environ.get("SH_EVIDENCE_DIR")
        if evidence:
            out = Path(evidence)
            out.mkdir(parents=True, exist_ok=True)
            (out / "ha11-summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            shutil.copytree(orchestration_root, out / "orchestration-root", dirs_exist_ok=True)

    status = summary["mission"]["status"]  # type: ignore[index]
    assert "waiting_on_person" not in summary, summary.get("waiting_on_person")
    assert status in {"COMPLETED", "FAILED"}, f"not terminal after {summary['elapsed_s']} s: {status}"
