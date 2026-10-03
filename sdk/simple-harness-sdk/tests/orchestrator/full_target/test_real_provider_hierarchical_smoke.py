# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3c part 2b · a hierarchical Mission on a real model (opt in: ``--run-real-provider``).

What it asserts is the **closure**, never the wording of a stochastic model:

* the hierarchical Planner round is *readable* — a run in which ``proposal_unreadable``
  appears at all is a failure of this slice, whatever the Mission's final status;
* no leaf is accepted having filed nothing at a declared output port (P2.3d / D3);
* the Mission ends either ``COMPLETED`` or with an **honest** stop: not completed, and
  every refusal carries a structured, machine-readable reason.

Credentials come from ``SH_BASEURL`` / ``SH_APIKEY`` / ``SH_MODEL`` and never reach the
evidence directory.  The raw receipts go to ``.local-test-evidence/`` (ignored); the
journal keeps only the text conclusion.

2026-10-03（HTN 补齐阶段 A′，分诊表：换芯）：世界换成产品同形部署上的代码领域测试世界
（:mod:`code_domain_world`：建任务时初始化根、绑定执行图、走保证通道、原生执行池），不再
``install_hierarchical(planning=)``、手插根任务与义务、手跑观察。偏离：

* 没有真实工作树上的观察器（产品部署 ``observers=()``；带观察器的测试世界等阶段 D），做法由真实
  模型**提出**、经独立审阅后采用（代码领域种子做法的判据号对不上产品根的要求号）；
* 用量走 ``product_world`` 的认证测试计数器（一词一个 token），报告里的 token 数不是真实用量；
* 报告里旧根审阅员的几项（切分、作废、预算用尽）随旧根审阅员删除，改记保证通道的审阅包与结论。

本轮只确认能收集、默认跳过；没有真调模型。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
_AGENTS = Path(__file__).resolve().parents[2] / "agents"
for _extra in (_HERE, _AGENTS):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from code_domain_world import code_world  # noqa: E402
from h1i_seed import run_until  # noqa: E402
from real_provider_config import build_real_provider, resolve_real_provider  # noqa: E402

from agent_orchestrator.contracts import MissionStatus  # noqa: E402
from agent_orchestrator.planning.htn.planner_package import fact_rows  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402

pytestmark = pytest.mark.real_provider

#: The test that genuinely **fails** against the seeded implementation.
FAILING_TEST = "tests/test_kv.py::test_parse_kv_strips_whitespace"

#: Where the raw receipts go.  Ignored by git; the journal records only the
#: conclusion, the ids and the token count — never the key and never a transcript.
EVIDENCE_ROOT = Path(
    os.environ.get(
        "HTN_SMOKE_EVIDENCE",
        str(Path(__file__).resolve().parents[3] / ".local-test-evidence" / "2026-09-16" / "htn-smoke"),
    )
)

PACKAGE = '''"""A tiny package with one failing test, for the P2.3c smoke run."""


def parse_kv(text):
    if not text:
        return {}
    out = {}
    for chunk in text.split(";"):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        out[key] = value
    return out
'''

FAILING = """from kvlib import parse_kv


def test_parse_kv_splits_pairs():
    assert parse_kv("a=1;b=2") == {"a": "1", "b": "2"}


def test_parse_kv_strips_whitespace():
    assert parse_kv(" a = 1 ; b = 2 ") == {"a": "1", "b": "2"}
"""

TERMINAL = {"COMPLETED", "FAILED", "STOPPED", "CANCELLED"}


def _root_parameters(_mission: Any) -> dict[str, Any]:
    # Relative names in the Mission's own workspace, never a host path (分诊裁决⑧-2).
    return {"repository": "kvlib", "failing_test": FAILING_TEST}


async def smoke(tmp_path: Path, provider: Any, *, timeout: float = 1800.0) -> tuple[dict[str, Any], Any]:
    """Run one code Mission on the product deployment with ``provider`` and report."""

    async with code_world(tmp_path, provider, root_parameters=_root_parameters, default_max_output_tokens=4096,
                          test_timeout_seconds=120, turn_deadline_seconds=600) as world:
        store = world.store
        mission_id = world.create({
            "goal": "修复仓库里失败的测试 tests/test_kv.py::test_parse_kv_strips_whitespace，并说明改动；不要改测试本身。",
            "success_criteria": ["pytest:tests/test_kv.py", "改动有说明"],
            "idempotency_key": f"htn-smoke-{os.getpid()}",
            # A minimal hierarchical plan is a handful of leaves plus room for one repair each.
            "budget": {"max_tokens": 600_000, "max_attempts": 12},
            "workspace_seed": {"kvlib.py": PACKAGE, "tests/test_kv.py": FAILING},
        })["mission_id"]
        try:
            await run_until(world, lambda: str(store.get_mission(mission_id).status.value) in TERMINAL,
                            timeout=timeout)
        except TimeoutError:
            pass  # an honest ending is judged below; a loop still running is reported as it is
        final = store.get_mission(mission_id)
        semantics = HtnStore(store)
        events = list(store.list_events(mission_id))
        rejections = [{"reason": item.payload.get("reason"), "detail": item.payload.get("detail")}
                      for item in events if item.type in {"PlanningRejected", "TaskGraphRejected", "PlanCommitRefused"}]
        ports: dict[str, list[str]] = {}
        for row in semantics.list_acceptance_outputs(mission_id):
            ports.setdefault(str(row["acceptance_id"]), []).append(str(row["output_port"]))
        report = {
            "mission_id": mission_id,
            "status": str(final.status),
            "stop_reason": final.stop_reason,
            "planner_rounds": len([i for i in store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED",
                                                                 "SETTLED", "FAILED")
                                   if i.mission_id == mission_id and i.kind == "plan"]),
            "event_types": sorted({item.type for item in events}),
            "rejections": rejections,
            "proposal_unreadable": any(item["reason"] == "proposal_unreadable" for item in rejections),
            "plan_revisions": len(semantics.list_plan_revisions(mission_id)),
            "package_facts": [item["observation_ref"] for item in fact_rows(semantics.list_observations(mission_id))[0]],
            "stalled": [item.payload for item in events if item.type == "HierarchicalMissionStalled"],
            "reviews": [
                {"package_id": str(package.package_id), "purpose": str(package.purpose),
                 "verdicts": [str(stored.record.verdict) for stored in semantics.list_review_records(str(package.package_id))]}
                for package in semantics.list_review_packages(mission_id)
            ],
            "accepted_outputs": [
                {"task_id": str(item.task_id), "ports": ports.get(str(item.acceptance_id), [])}
                for item in semantics.list_acceptances(mission_id)
            ],
            "progress": world.loop.progress_log[-40:],
        }
        return report, final


def test_real_hierarchical_planner_round(tmp_path):
    config = resolve_real_provider()
    if config is None:
        pytest.skip("no real provider configured (SH_BASEURL/SH_APIKEY or Host .env)")
    provider = build_real_provider(config, timeout=240.0)
    report, final = asyncio.run(smoke(tmp_path, provider))
    report["model"] = config.model
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_ROOT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("HTN_SMOKE_REPORT " + json.dumps(report, ensure_ascii=False, default=str))

    # The property this slice owns: the hierarchical Planner round is readable.
    assert not report["proposal_unreadable"], (
        "the hierarchical Planner round came back unreadable — the package and the "
        f"prompt are still not agreeing: {report['rejections']}"
    )
    # P2.3d / defect D3: no leaf may be accepted with nothing filed at a declared port.
    empty = [item for item in report["accepted_outputs"] if not item["ports"]]
    assert report["accepted_outputs"], "at least one leaf was accepted in this run"
    assert not empty, f"a leaf was accepted having claimed no declared output port: {empty}"
    # Closure: completed, or an honest ending — a stop that names its reason, or a
    # recorded stall that names every gate still holding an occurrence.
    if final.status is not MissionStatus.COMPLETED:
        assert all(item["reason"] for item in report["rejections"]), (
            "every refusal must carry a machine-readable reason"
        )
        if final.status is MissionStatus.FAILED:
            assert final.stop_reason is not None, "a stop without a reason is not an honest failure"
        else:
            assert report["stalled"], "the Mission neither finished nor stopped and nothing said why"
            for record in report["stalled"]:
                assert record["withheld"], "a stall record with no refusal explains nothing"
