# SPDX-License-Identifier: Apache-2.0
"""全业务重放 v3 审计插件（HTN 补齐阶段 G；取代 v2 的 ``p33_replay_audit``）。

每条用例结束后，在它的临时目录里找编排库（``orchestrator.db``），只读打开，按 v3 核：全库
检查（只增表与回执账每行恰好被点名一次）+ 两库对照（同目录的执行库）+ 每个任务逐表重建比对。会话结束把汇总写成 JSON。

    PYTHONPATH=tests/orchestrator/product_world uv run --frozen pytest -p replay_v3_audit \\
        --replay-v3-audit=.local-test-evidence/<日期>/g-replay/audit.json [--replay-v3-audit-strict] ...

用例故意改坏库、或绕过产品路径直接写库来造局面的，标 ``@pytest.mark.replay_audit_exempt("理由")``，
报告记"豁免"与理由，不算失败。默认只观察，不改变用例结果。``--replay-v3-audit-strict`` 时，有任何"不一致"（含静默改动）、
或全库检查不一致，整轮判失败。范围外的任务（按别的口径建的）如实计数，不算失败。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.observability.business_replay import (
    CONSISTENT,
    INCONSISTENT,
    OUT_OF_SCOPE,
    verify_execution_ledgers,
    verify_library,
    verify_mission,
)
from agent_orchestrator.storage.store import Store

_RESULTS: list[dict[str, Any]] = []


def audit_database(path: Path) -> dict[str, Any]:
    """One closed library: the library-wide check plus every Mission's report (read only)."""

    store = Store.open_readonly(path)
    try:
        missions = [row[0] for row in store.connection.execute(
            "SELECT mission_id FROM missions ORDER BY created_at")]
        reports = {mission_id: verify_mission(store, mission_id) for mission_id in missions}
        library = verify_library(store)
        ledgers = verify_execution_ledgers(store, sorted(path.parent.glob("execution*.db")))
        volume = [(row[0], int(row[1]), int(row[2])) for row in store.connection.execute(
            "SELECT mission_id, count(*), sum(length(payload_json)) FROM events"
            " WHERE type='RowsWritten' AND mission_id != 'deployment' GROUP BY mission_id")]
    finally:
        store.close()
    statuses = Counter(report["status"] for report in reports.values())
    bad_tables = sorted({f"{name}:{item['status']}" for report in reports.values()
                         for name, item in report["tables"].items()
                         if item["status"] == INCONSISTENT})
    failed = library["status"] != CONSISTENT or ledgers["status"] != CONSISTENT or statuses[INCONSISTENT]
    return {"database": str(path), "library": library["status"], "unnamed": library["unnamed_count"],
            "execution_ledgers": {k: v for k, v in ledgers.items() if v},
            # 偏差裁决 1 R9：整行事件最多的那个任务的条数与字节
            "rows_written_max": max(({"mission_id": m, "events": n, "bytes": b} for m, n, b in volume),
                                    key=lambda item: item["bytes"], default=None),
            "missions": dict(sorted(statuses.items())), "tables_not_consistent": bad_tables,
            "status": INCONSISTENT if failed else CONSISTENT,
            "out_of_scope": statuses[OUT_OF_SCOPE]}


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("replay-v3-audit")
    group.addoption("--replay-v3-audit", default=None, help="write the v3 replay audit JSON here")
    group.addoption("--replay-v3-audit-strict", action="store_true", default=False,
                    help="fail the run when any audited library is not consistent")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: Any, nextitem: Any) -> Any:
    yield
    if not item.config.getoption("--replay-v3-audit"):
        return
    root = item.funcargs.get("tmp_path") if hasattr(item, "funcargs") else None
    if root is None:
        return
    exempt = item.get_closest_marker("replay_audit_exempt")
    if exempt is not None:  # 用例故意改坏库，或绕过产品路径直接写库来造局面：如实记"豁免"与理由
        _RESULTS.append({"test": item.nodeid, "status": "EXEMPT",
                         "reason": (exempt.args or exempt.kwargs.get("reason", ("",)))[0]})
        return
    for database in sorted(Path(root).rglob("orchestrator.db")):
        try:
            result = audit_database(database)
        except Exception as error:  # noqa: BLE001 - an unreadable library is itself a finding
            result = {"database": str(database), "status": "UNREADABLE",
                      "error": f"{type(error).__name__}: {error}"}
        _RESULTS.append({"test": item.nodeid, **result})


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    target = session.config.getoption("--replay-v3-audit")
    if not target:
        return
    failed = [row for row in _RESULTS if row["status"] not in {CONSISTENT, "EXEMPT"}]
    summary = {"databases": len(_RESULTS), "not_consistent": len(failed),
               "exempt": sum(1 for row in _RESULTS if row["status"] == "EXEMPT"),
               "missions": dict(sum((Counter(row.get("missions") or {}) for row in _RESULTS), Counter())),
               "results": _RESULTS}
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if failed and session.config.getoption("--replay-v3-audit-strict"):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
