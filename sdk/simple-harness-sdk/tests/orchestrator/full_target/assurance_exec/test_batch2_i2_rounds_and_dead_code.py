# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""第 2 批车道 I2：审阅轮次（A15）与残留死代码清理的守护（A24）。

* A15 审阅绑定的 ``round_no`` 不再恒为 1：同一个审阅对象（任务 × 用途 × 出现）再准备一份新审阅包
  就是新一轮，轮次 = 已有绑定的最大轮次 + 1（计划 #144/#176：格式修复与复审调用是同一轮的
  ordinal 2，不是新 round；返工后的新包、显式独立二审是新 round/package）。
* A24 删掉的符号不再回来（改坏检验由"源码扫描无该符号"代替）。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from agent_orchestrator.orchestrator.assurance_purpose_reviews import next_review_round

SRC = Path(__file__).resolve().parents[4] / "src" / "agent_orchestrator"


def _bindings_table() -> sqlite3.Connection:
    schema = (SRC / "storage" / "assurance_schema.sql").read_text()
    start = schema.index("CREATE TABLE assurance_review_bindings")
    statement = schema[start : schema.index(") STRICT;", start) + len(") STRICT;")]
    connection = sqlite3.connect(":memory:")
    connection.execute(statement)  # foreign keys are off in a bare connection: only the shape matters here
    return connection


def _binding(connection, *, review_key, mission="m-1", owner="task-a", purpose="TASK_CONTENT",
             occurrence="occ-1", round_no=1, command="cmd-" + "x"):
    body = {"subject": {"purpose": purpose, "occurrence_id": occurrence}}
    import json
    connection.execute(
        "INSERT INTO assurance_review_bindings(review_key,mission_id,package_id,owner_task_id,request_command_id,"
        "round_no,requirements_revision,requirements_hash,subject_hash,input_manifest_hash,binding_hash,binding_json,"
        "source_receipt_id,created_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (review_key, mission, "pkg-" + review_key, owner, command + review_key, round_no, 1,
         "a" * 64, "b" * 64, "c" * 64, "d" * 64, json.dumps(body), "receipt-" + review_key, 1),
    )


# --------------------------------------------------------------------------- A15
def test_a15_a_new_package_for_the_same_subject_is_the_next_round():
    """没有绑定 → 第 1 轮；同任务同用途同出现已有第 1、2 轮 → 第 3 轮；别的用途 / 别的出现 / 别的任务 /
    别的任务（mission）不算；本 review_key 自己的重放不把自己算成上一轮。

    **改坏检验**：PR/CR 把 ``round_no`` 写回常量 1 → 下面的守护用例 ``test_a15_binding_writers_take_the_round_from_the_counter`` 变红；
    计数器忽略用途 → 本用例"别的用途不算"一处变红。"""
    connection = _bindings_table()
    kw = dict(mission_id="m-1", review_key="rk-new", purpose="TASK_CONTENT", owner_task_id="task-a", occurrence_id="occ-1")
    assert next_review_round(connection, **kw) == 1
    _binding(connection, review_key="rk-1", round_no=1)
    _binding(connection, review_key="rk-2", round_no=2)
    assert next_review_round(connection, **kw) == 3
    # 别的用途、别的出现、别的任务、别的 mission 的绑定不算这个对象的轮次
    _binding(connection, review_key="rk-p", purpose="METHOD_PLAN", round_no=7)
    _binding(connection, review_key="rk-o", occurrence="occ-2", round_no=7)
    _binding(connection, review_key="rk-t", owner="task-b", round_no=7)
    _binding(connection, review_key="rk-m", mission="m-2", round_no=7)
    assert next_review_round(connection, **kw) == 3
    # 重放：本 review_key 已经是第 3 轮，再算一次仍是 3
    _binding(connection, review_key="rk-new", round_no=3)
    assert next_review_round(connection, **kw) == 3
    # 出现为空（用途审阅可以没有 occurrence）按 IS NULL 匹配
    _binding(connection, review_key="rk-n", occurrence=None, purpose="MISSION_FINAL", round_no=1)
    assert next_review_round(connection, mission_id="m-1", review_key="rk-x", purpose="MISSION_FINAL",
                             owner_task_id="task-a", occurrence_id=None) == 2


def test_a15_binding_writers_take_the_round_from_the_counter():
    """两处写绑定的地方（用途审阅、内容审阅）都从计数器取轮次，不再写常量 1。"""
    for name in ("assurance_purpose_reviews.py", "assurance_content_review.py"):
        text = (SRC / "orchestrator" / name).read_text()
        assert '"round_no": round_no' in text, name
        assert '"round_no": 1' not in text, name
        assert "next_review_round(" in text, name


# --------------------------------------------------------------------------- A24
_GONE = (
    "consume_fuel", "ExpansionRecord", "FuelDecision",              # 按次扣燃料：只给测试用
    "reevaluate_consumer", "LineageRecord", "ConsumerUse",          # 无生产调用方
    "find_duplicates", "DuplicateReport",                           # 无产品调用方
    "RootReviewRequest", "REQUIREMENTS_REVISION_SEMANTICS", "excerpt_of",  # 旧根审阅请求
    "PARENT_COMPOUND_TASK", "parent_compound_task",                 # 合同账户名与实际一致
    "CLOSEOUT_ROOT_NETWORK_UNAVAILABLE",                            # 不可达分支的码
    "merge_accepted", "collect_upstream_inputs", "materialise_inputs",  # 全祖先扫描（主会话补派）
)


def test_a24_removed_symbols_do_not_come_back():
    """源码扫描：删掉的死代码符号在 ``src/`` 里一个都不剩（表结构 CHECK 常量文本除外，见记录）。"""
    # review_packages.review_account 的 CHECK 常量列表留在建表文本里：改已发布迁移的 DDL 文本会让
    # 旧库的迁移校验和对不上；多出的允许值无害。
    allowed = {("storage/htn_schema.py", "parent_compound_task")}
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        for symbol in _GONE:
            if re.search(r"\b" + re.escape(symbol) + r"\b", text) and (str(path.relative_to(SRC)), symbol) not in allowed:
                offenders.append((str(path.relative_to(SRC)), symbol))
    assert offenders == []
    assert "def normalise_goal" in (SRC / "graph" / "deduplicator.py").read_text()


def test_a24_unreachable_branches_and_stale_wording_are_gone():
    commit_service = (SRC / "orchestrator" / "commit_service.py").read_text()
    assert "def finalize_assured_mission" not in commit_service  # 包装无调用者；唯一终写在 assurance_final_writer
    start = commit_service.index("def _require_root_resolution")
    assert "if network is None" not in commit_service[start : start + 2500]
    consumers = (SRC / "orchestrator" / "assurance_consumers.py").read_text()
    assert "if network is None" not in consumers
    assert "def request(" not in (SRC / "orchestrator" / "root_review.py").read_text()
    assert "releases the terminal pools" not in (SRC / "orchestrator" / "assurance_final_writer.py").read_text()
    resolution = (SRC / "contracts" / "resolution.py").read_text()
    assert "ReviewPurpose.COMPOSITION: ReviewAccount.MISSION" in resolution
