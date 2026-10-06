# -*- coding: utf-8 -*-
"""run.py 判定口径的离线自测（不起后台、不调模型，用假结果喂判定函数）。

三条口径（原计划 §15 / model-scenarios.json）：
1. 预算从 model-scenarios.json 读，脚本里没有自己的数字；
2. 超预算的局判 FAIL 并写明原因；
3. 同局号再跑写成新的 attempt 目录，旧的原样保留。

另有 V28"两条线共用一步、其中一条换做法"的判定：用造好的假库行（只建判定读到的那几张表）喂
``oracle_shared``，核通过、超预算、共用步骤重复执行判 FAIL、观察项只记不判。

跑法（在仓库根）::

    backend/.venv/bin/python backend/scripts/assurance_model_scenarios/test_verdict_offline.py
    # 或 backend/.venv/bin/python -m pytest -q backend/scripts/assurance_model_scenarios/test_verdict_offline.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402

GOOD_ORACLE = {"root_resolution_accepted": True, "closeout_finalized": True, "report_accepted": True}


def _usage(tokens: int, calls: int) -> dict:
    return {"model_calls": calls, "tokens": tokens, "token_column": "settled_tokens"}


def test_limits_come_only_from_plan_json() -> None:
    body = json.loads(run.PLAN_SCENARIOS.read_text(encoding="utf-8"))["limits"]
    assert run.LIMITS == {"tokens": body["total_model_tokens"], "model_calls": body["model_calls"],
                          "tool_calls": body["tool_calls"], "wall": body["wall_time_seconds"]}
    # 五个场景没有自己的预算；换一份 JSON 预算就跟着变，证明数字不在脚本里
    assert all("limits" not in spec for spec in run.SCENARIOS.values())
    with tempfile.TemporaryDirectory() as tmp:
        other = Path(tmp) / "scenarios.json"
        other.write_text(json.dumps({"limits": {"total_model_tokens": 7, "model_calls": 1, "tool_calls": 2,
                                                "wall_time_seconds": 3}}), encoding="utf-8")
        assert run.plan_limits(other) == {"tokens": 7, "model_calls": 1, "tool_calls": 2, "wall": 3}


def test_within_budget_and_all_checks_pass() -> None:
    limits = {"tokens": 300_000, "model_calls": 16, "tool_calls": 32, "wall": 900}
    result = run.verdict("COMPLETED", GOOD_ORACLE, _usage(210_000, 9), 180.0, limits)
    assert result == {"passed": True, "within_budget": True, "fail_reasons": []}


def test_over_budget_is_a_failed_trial_with_reason() -> None:
    limits = {"tokens": 300_000, "model_calls": 16, "tool_calls": 32, "wall": 900}
    # 任务完成、判定全对，只是 token 超了 → FAIL
    result = run.verdict("COMPLETED", GOOD_ORACLE, _usage(420_000, 9), 240.0, limits)
    assert result["passed"] is False and result["within_budget"] is False
    assert any("token 总量 420000 > 上限 300000" in r for r in result["fail_reasons"])
    # 模型调用数超 → FAIL
    result = run.verdict("COMPLETED", GOOD_ORACLE, _usage(100_000, 17), 240.0, limits)
    assert result["passed"] is False and any("模型调用数 17 > 上限 16" in r for r in result["fail_reasons"])
    # 墙钟超 → FAIL
    result = run.verdict("COMPLETED", GOOD_ORACLE, _usage(100_000, 9), 901.0, limits)
    assert result["passed"] is False and any("墙钟秒数 901 > 上限 900" in r for r in result["fail_reasons"])
    # 用量缺失不能当作在预算内
    result = run.verdict("COMPLETED", GOOD_ORACLE, {"model_calls": 9, "tokens": None}, 100.0, limits)
    assert result["passed"] is False and result["within_budget"] is False


def test_unfinished_or_wrong_oracle_still_fails() -> None:
    limits = {"tokens": 300_000, "model_calls": 16, "tool_calls": 32, "wall": 900}
    result = run.verdict("FAILED", GOOD_ORACLE, _usage(1000, 1), 10.0, limits)
    assert result["passed"] is False and result["within_budget"] is True
    assert any("不是 COMPLETED" in r for r in result["fail_reasons"])
    result = run.verdict("COMPLETED", {**GOOD_ORACLE, "total": False}, _usage(1000, 1), 10.0, limits)
    assert result["passed"] is False and any("判定 total 未成立" in r for r in result["fail_reasons"])


def test_rerun_same_trial_keeps_old_attempt() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        first, n1 = run.new_attempt_dir(out, "accurate-report", 2)
        assert n1 == 1 and first == out / "accurate-report/trial-2/attempt-1"
        (first / "record.json").write_text('{"attempt": 1, "passed": false}', encoding="utf-8")
        second, n2 = run.new_attempt_dir(out, "accurate-report", 2)
        assert n2 == 2 and second == out / "accurate-report/trial-2/attempt-2"
        # 旧记录一个字节没动，新目录是空的
        assert (first / "record.json").read_text(encoding="utf-8") == '{"attempt": 1, "passed": false}'
        assert list(second.iterdir()) == []
        # 别的局号互不影响
        third, n3 = run.new_attempt_dir(out, "accurate-report", 3)
        assert n3 == 1 and third.parent.name == "trial-3"


# ------------------------------------------------------------------ V28：两条线共用一步、其中一条换做法
SPEC = run.SCENARIOS["shared-step-switch-method"]
COSTS = json.dumps({"rows": [{"item": "A", "cost": 120}, {"item": "B", "cost": 100}], "total_cost": 220,
                    "source_sha256": run.sha(run.FACTS_V1)}, ensure_ascii=False)
FINANCE = "item,cost\nA,120\nB,100\nTOTAL,220\n"
SUMMARY = "| item | cost |\n| --- | --- |\n| A | 120 |\n| B | 100 |\n\n合计：220 元\n"
MISSION = "mission-v28"
LIMITS = {"tokens": 300_000, "model_calls": 16, "tool_calls": 32, "wall": 900}

SCHEMA = """
CREATE TABLE goal_resolutions (mission_id TEXT, verdict TEXT, validity TEXT);
CREATE TABLE assurance_closeouts (mission_id TEXT, state TEXT);
CREATE TABLE review_records (mission_id TEXT, official INTEGER, verdict TEXT, record_json TEXT);
CREATE TABLE actions (mission_id TEXT, json TEXT);
CREATE TABLE acceptances (mission_id TEXT, task_id TEXT, validity TEXT, acceptance_json TEXT);
CREATE TABLE artifacts (artifact_id TEXT, mission_id TEXT, task_id TEXT, path TEXT, content_hash TEXT);
CREATE TABLE plan_revisions (mission_id TEXT, revision INTEGER, state TEXT);
CREATE TABLE plan_memberships (mission_id TEXT, revision INTEGER, occurrence_id TEXT, task_id TEXT, form TEXT,
                               adopted INTEGER);
CREATE TABLE method_instances (mission_id TEXT, instance_id TEXT, goal_occurrence_id TEXT, method_id TEXT,
                               method_version INTEGER, method_content_hash TEXT, plan_revision INTEGER, state TEXT,
                               created_at REAL);
CREATE TABLE method_child_occurrences (mission_id TEXT, instance_id TEXT, slot_key TEXT, occurrence_id TEXT,
                                       reuse_policy TEXT);
"""


class World:
    """假库：只建判定读到的表；``branches=True`` 时造 SDK 用例 test_the_branch_that_reuses_a_step_can_change_its_
    method_and_keep_sharing_it 的形状（根 → 左右两个子目标；右边点名共用左边算明细那一步；右边被打回后换做法、
    仍点名共用）；``branches=False`` 时造 10-05 联测那种形状（根做法里明细排成前置步骤，没有点名共用、没有换做法）。"""

    def __init__(self, root: Path, *, branches: bool = True) -> None:
        self.backend = SimpleNamespace(userdata=root / "userdata", run_dir=root, published=root / "published")
        base = self.backend.userdata / "data/agent-orchestrator"
        (base / "artifacts/sha256").mkdir(parents=True)
        self.base = base
        self.db = sqlite3.connect(base / "orchestrator.db")
        self.db.executescript(SCHEMA)
        self.n = 0
        self.db.execute("INSERT INTO goal_resolutions VALUES (?,?,?)", (MISSION, "ACCEPT", "CURRENT"))
        self.db.execute("INSERT INTO assurance_closeouts VALUES (?,?)", (MISSION, "FINALIZED"))
        for _ in range(2):
            self.db.execute("INSERT INTO review_records VALUES (?,?,?,?)", (MISSION, 1, "ACCEPTED", "{}"))
        if branches:
            self.revision(0, "RETIRED", {"occ-costs": "t-costs", "occ-fin": "t-fin", "occ-sum": "t-sum"})
            self.revision(1, "ACTIVE", {"occ-costs": "t-costs", "occ-fin": "t-fin", "occ-sum2": "t-sum2"})
            self.instance("mi-root", "occ-root", "m-root", 1, 0, "ADOPTED", {"left": ("occ-left", "new_work"),
                                                                             "right": ("occ-right", "new_work")})
            self.instance("mi-left", "occ-left", "m-left", 1, 0, "ADOPTED", {"costs": ("occ-costs", "new_work"),
                                                                             "fin": ("occ-fin", "new_work")})
            self.instance("mi-right", "occ-right", "m-right", 1, 0, "RETIRED", {"costs": ("occ-costs", "share_active"),
                                                                                "sum": ("occ-sum", "new_work")})
            self.instance("mi-right2", "occ-right", "m-right", 2, 1, "ADOPTED", {"costs": ("occ-costs", "reuse_accepted"),
                                                                                 "sum": ("occ-sum2", "new_work")})
            self.accept("t-costs", {"costs.json": COSTS})
            self.accept("t-fin", {"finance.csv": FINANCE})
            self.accept("t-sum", {"summary.md": run.BAD_SUMMARY}, validity="REVOKED")  # 被打回的错稿
            self.accept("t-sum2", {"summary.md": SUMMARY})
        else:
            self.revision(0, "ACTIVE", {"occ-costs": "t-costs", "occ-fin": "t-fin", "occ-sum": "t-sum"})
            self.instance("mi-root", "occ-root", "m-root", 1, 0, "ADOPTED", {
                "costs": ("occ-costs", "new_work"), "fin": ("occ-fin", "new_work"), "sum": ("occ-sum", "new_work")})
            self.accept("t-costs", {"costs.json": COSTS})
            self.accept("t-fin", {"finance.csv": FINANCE})
            self.accept("t-sum", {"summary.md": SUMMARY})
        self.db.commit()

    def revision(self, revision: int, state: str, members: dict[str, str]) -> None:
        self.db.execute("INSERT INTO plan_revisions VALUES (?,?,?)", (MISSION, revision, state))
        for occurrence, task in members.items():
            self.db.execute("INSERT INTO plan_memberships VALUES (?,?,?,?,?,?)",
                            (MISSION, revision, occurrence, task, "primitive", 1))

    def instance(self, instance: str, goal: str, method: str, version: int, revision: int, state: str,
                 children: dict[str, tuple[str, str]]) -> None:
        self.db.execute("INSERT INTO method_instances VALUES (?,?,?,?,?,?,?,?,?)",
                        (MISSION, instance, goal, method, version, f"hash-{method}-{version}", revision, state, revision))
        for slot, (occurrence, policy) in children.items():
            self.db.execute("INSERT INTO method_child_occurrences VALUES (?,?,?,?,?)",
                            (MISSION, instance, slot, occurrence, policy))

    def accept(self, task: str, files: dict[str, str], *, validity: str = "CURRENT") -> None:
        refs = []
        for path, text in files.items():
            self.n += 1
            digest = run.sha(text)
            (self.base / "artifacts/sha256" / digest).write_text(text, encoding="utf-8")
            self.db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?)", (f"art-{self.n}", MISSION, task, path, digest))
            refs.append({"id": f"art-{self.n}", "content_hash": digest})
        self.db.execute("INSERT INTO acceptances VALUES (?,?,?,?)",
                        (MISSION, task, validity, json.dumps({"artifact_refs": refs})))
        self.db.commit()

    def judge(self, *, tokens: int = 200_000, calls: int = 12, wall: float = 400.0) -> dict:
        record = {"scenario": "shared-step-switch-method", "mission_id": MISSION, "final_status": "COMPLETED",
                  "hooks": [{"hook": "swap_first_report", "event": "installed"},
                            {"hook": "swap_first_report", "path": "summary.md", "swapped": True}],
                  "events_seen": [], "triggers": []}
        record["oracle"] = run.oracle_shared(self.backend, record, SPEC)
        record["usage"] = {"model_calls": calls, "tokens": tokens}
        record.update(run.verdict("COMPLETED", record["oracle"], record["usage"], wall, LIMITS))
        return record


def test_v28_prompt_offers_sharing_by_structure_only() -> None:
    """架构核心思想：题目只给结构上的共用机会，不点名要求"共用 / 复用 / 点名 / 换做法"；产品不加规则。"""
    text = SPEC["goal"] + "".join(SPEC["criteria"])
    for word in ("共用", "复用", "重用", "点名", "reuse", "换做法", "share"):
        assert word not in text, word
    assert SPEC["hook"] == "swap_first_report" and SPEC["hook_target"] == "summary.md"
    assert SPEC["bad_content"] == run.BAD_SUMMARY and "290" in run.BAD_SUMMARY
    assert "limits" not in SPEC  # 预算同样只从 model-scenarios.json 读


def test_v28_passes_and_observes_named_sharing_and_switch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        record = World(Path(tmp)).judge()
    assert all(v is True for v in record["oracle"].values()), record["oracle"]
    assert record["passed"] is True and record["fail_reasons"] == []
    assert record["observed"] == {"named_sharing": True, "method_switched": True}
    [shared] = record["observed_detail"]["named_shared_steps"]
    assert shared["occurrence_id"] == "occ-costs" and shared["task_id"] == "t-costs"
    assert shared["held_by_goals"] == ["occ-left", "occ-right"] and shared["adopted_holders"] == 2
    [switch] = record["observed_detail"]["method_switches"]
    assert switch["is_branch"] and switch["goal_occurrence_id"] == "occ-right"
    assert (switch["retired"], switch["replacement"]) == ("m-right@1", "m-right@2")
    assert switch["keeps_shared_steps"] == ["occ-costs"]
    # 观察项不进判定：checks 里没有它们
    assert not {"named_sharing", "method_switched"} & set(record["oracle"])


def test_v28_over_budget_fails_even_with_both_observed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        record = World(Path(tmp)).judge(tokens=420_000)
    assert record["observed"] == {"named_sharing": True, "method_switched": True}
    assert record["passed"] is False and record["within_budget"] is False
    assert any("token 总量 420000 > 上限 300000" in r for r in record["fail_reasons"])


def test_v28_shared_step_executed_twice_fails() -> None:
    """两条线各自算了一遍 costs.json、两份都在现行计划里被验收 → 共用步骤重复执行 → FAIL。"""
    with tempfile.TemporaryDirectory() as tmp:
        world = World(Path(tmp))
        world.db.execute("INSERT INTO plan_memberships VALUES (?,?,?,?,?,?)",
                         (MISSION, 1, "occ-costs-again", "t-costs-again", "primitive", 1))
        world.accept("t-costs-again", {"costs.json": COSTS})
        record = world.judge()
    assert record["oracle"]["shared_step_accepted_once"] is False
    assert {task for task, _ in record["shared_output_acceptances"]} == {"t-costs", "t-costs-again"}
    assert record["passed"] is False
    assert any("判定 shared_step_accepted_once 未成立" in r for r in record["fail_reasons"])


def test_v28_an_old_step_outside_the_current_plan_is_not_a_duplicate() -> None:
    """接替 / 换做法后旧步骤的验收仍是 CURRENT 但已不在现行计划里：是历史，不算重复执行。"""
    with tempfile.TemporaryDirectory() as tmp:
        world = World(Path(tmp))
        world.accept("t-costs-old", {"costs.json": COSTS})  # 不在任何现行计划版本里
        record = world.judge()
    assert record["oracle"]["shared_step_accepted_once"] is True and record["passed"] is True


def test_v28_bad_draft_accepted_or_wrong_content_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        world = World(Path(tmp))
        world.db.execute("UPDATE acceptances SET validity='CURRENT' WHERE task_id='t-sum'")
        world.db.execute("UPDATE plan_memberships SET task_id='t-sum' WHERE task_id='t-sum2'")
        world.db.commit()
        record = world.judge()
    assert record["oracle"]["bad_draft_never_accepted"] is False and record["oracle"]["summary_content"] is False
    assert record["passed"] is False
    with tempfile.TemporaryDirectory() as tmp:
        world = World(Path(tmp))
        world.db.execute("DELETE FROM acceptances WHERE task_id='t-fin'")
        world.accept("t-fin", {"finance.csv": "item,cost\nA,120\nB,100\n"})  # 少了 TOTAL 行
        record = world.judge()
    assert record["oracle"]["finance_content"] is False and record["passed"] is False


def test_v28_observations_are_recorded_not_judged() -> None:
    """10-05 联测那种做法（明细排成前置步骤、没有点名共用、没有换做法）：照样 PASS，观察项如实记"没观察到"。"""
    with tempfile.TemporaryDirectory() as tmp:
        record = World(Path(tmp), branches=False).judge()
        line = run.summary_line(record, Path(tmp))
    assert record["passed"] is True, record["fail_reasons"]
    assert record["observed"] == {"named_sharing": False, "method_switched": False}
    assert line["observed"] == record["observed"] and line["passed"] is True
    assert run.observed_text(line["observed"]) == "点名共用：没观察到 / 换做法：没观察到"


def test_v28_root_replacement_keeping_a_step_is_not_two_branch_sharing() -> None:
    """10-05 联测库里真有的形状：根做法换版本、新版本沿用已验收的步骤（reuse_accepted）——同一个目标的新旧
    实例，不是两条线共用；根换做法也不算"某条分支换做法"，但细节里如实列出。"""
    with tempfile.TemporaryDirectory() as tmp:
        world = World(Path(tmp), branches=False)
        world.db.execute("UPDATE method_instances SET state='RETIRED' WHERE instance_id='mi-root'")
        world.instance("mi-root2", "occ-root", "m-root", 2, 1, "ADOPTED", {
            "costs": ("occ-costs", "reuse_accepted"), "fin": ("occ-fin", "new_work"), "sum": ("occ-sum", "new_work")})
        world.db.commit()
        record = world.judge()
    assert record["observed"] == {"named_sharing": False, "method_switched": False}
    [switch] = record["observed_detail"]["method_switches"]
    assert switch["goal_occurrence_id"] == "occ-root" and switch["is_branch"] is False
    assert record["passed"] is True


def test_v28_env_failure_line_says_observations_missing() -> None:
    record = {"scenario": "shared-step-switch-method", "final_status": "INVALID_ENV", "passed": False}
    line = run.summary_line(record, Path("/x"))
    assert "observed" in line and line["observed"] is None
    assert run.observed_text(None).startswith("没有记录")
    # 别的场景的行不多出观察项
    assert "observed" not in run.summary_line({"scenario": "accurate-report"}, Path("/x"))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"通过 {test.__name__}")
    print(f"{len(tests)} 条全部通过")
