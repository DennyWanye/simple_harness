# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""A6-6 判据修正(事件 T): 本 plan 新建的必须是 **target 流程节点**, 不是「随便一端」。

`--selftest` 的夹具里 `cognitive_relations` 恒为 0 行, 所以改写后的 `item_a6_6`
在自检里只会走到 INCONCLUSIVE, 判据本身一行没被执行过。本文件补上带关系行的夹具。

判据(见 `scripts/native/a6_verify.py::item_a6_6` 注释与
`plans/2026-09-08-hm-to-a6/DECISION-T-RELATION-FORM.md` §1):

* 只看 `relation_kind='applies_to'` 的知识边;
* relation memory 自身由本 plan 新建(revision 行的 `plan_id`/`plan_hash` 与关系行一致,
  且 `relation_memory_id` 在 `cognitive_memory_heads` 中存在);
* **target** 端点由本 plan 新建, 且 `cognitive_memory_heads.memory_type='procedure'`;
* 两端 exact revision 均能在 `cognitive_memory_revisions` 里解析。

第三条是本文件存在的理由: 旧写法只要求「至少一个端点」由本 plan 新建, 于是「本轮再造
一条同值 semantic 当 source + 连一条**旧**流程当 target」也会 PASS —— 那正是 §1 判掉的
重复槽位形状(事件 L 的 F-L4), 也和改写后的 A6-6 计划行相反。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

_VERIFY = (
    Path(__file__).resolve().parents[3] / "scripts" / "native" / "a6_verify.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("a6_verify_under_test_a6_6", _VERIFY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


a6 = _load()

# 本 plan(T15 那一轮) 与 T1 那一轮的身份。
PLAN = ("plan-t15", "hash-t15")
OLD_PLAN = ("plan-t1", "hash-t1")

# 形态 A 的三个节点: T1 的旧事实(source)、本轮新建的流程节点(target)、relation memory 自己。
FACT = "mem-fact"          # semantic, T1 建, revision 1
FLOW = "mem-flow"          # procedure, 本轮建, revision 1
RELMEM = "mem-relation"    # relation memory, 本轮建, revision 1
DUP = "mem-dup-claim"      # 本轮再造的同值 semantic(形态 B 的 source)
OLD_FLOW = "mem-old-flow"  # T1 就存在的流程节点

_HEADS = [
    (FACT, 1, "semantic"),
    (FLOW, 1, "procedure"),
    (RELMEM, 1, "semantic"),
    (DUP, 1, "semantic"),
    (OLD_FLOW, 1, "procedure"),
]
_REVISIONS = [
    (FACT, 1) + OLD_PLAN,
    (OLD_FLOW, 1) + OLD_PLAN,
    (FLOW, 1) + PLAN,
    (RELMEM, 1) + PLAN,
    (DUP, 1) + PLAN,
]


def _relation(
    *,
    relation_id="rel-1",
    plan=PLAN,
    kind="applies_to",
    relation_memory_id=RELMEM,
    relation_memory_revision=1,
    source=(FACT, 1),
    target=(FLOW, 1),
    created_at=100.0,
):
    return (
        relation_id, plan[0], plan[1], kind, relation_memory_id, relation_memory_revision,
        source[0], source[1], target[0], target[1], created_at,
    )


def _hm_db(tmp_path: Path, relations, *, with_relation_revision=True,
           heads=None, revisions=None) -> "a6.RoDb":
    path = tmp_path / "human_memory_v7.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "create table cognitive_relations(relation_id text primary key, plan_id text,"
        " plan_hash text, relation_kind text, relation_memory_id text,"
        + (" relation_memory_revision integer," if with_relation_revision else "")
        + " source_memory_id text, source_revision integer,"
        " target_memory_id text, target_revision integer, created_at real)"
    )
    conn.execute(
        "create table cognitive_memory_heads(memory_id text primary key,"
        " current_revision integer, memory_type text)"
    )
    conn.execute(
        "create table cognitive_memory_revisions(memory_id text, revision integer,"
        " plan_id text, plan_hash text)"
    )
    conn.executemany("insert into cognitive_memory_heads values (?,?,?)",
                     _HEADS if heads is None else heads)
    conn.executemany("insert into cognitive_memory_revisions values (?,?,?,?)",
                     _REVISIONS if revisions is None else revisions)
    if with_relation_revision:
        conn.executemany("insert into cognitive_relations values (?,?,?,?,?,?,?,?,?,?,?)",
                         relations)
    else:
        conn.executemany(
            "insert into cognitive_relations values (?,?,?,?,?,?,?,?,?,?)",
            [row[:5] + row[6:] for row in relations],
        )
    conn.commit()
    conn.close()
    return a6.RoDb(str(path), "human_memory_v7.db")


def _run(tmp_path: Path, relations, **kwargs) -> "a6.Item":
    db = _hm_db(tmp_path, relations, **kwargs)
    try:
        return a6.item_a6_6(SimpleNamespace(hm=db))
    finally:
        db.close()


# ------------------------------------------------------------------ (i) 形态 A: PASS
def test_new_workflow_target_plus_existing_fact_source_passes(tmp_path: Path) -> None:
    item = _run(tmp_path, [_relation()])
    assert item.verdict == a6.PASS, item.reason
    assert item.numbers["cognitive_relations_rows"] == 1
    assert item.numbers["applies_to_rows"] == 1
    assert item.numbers["relations_new_workflow_target_plus_relation_in_same_plan"] == 1
    assert item.numbers["matched_sample"][0]["endpoints_created_in_this_plan"] == ["target"]
    assert item.numbers["matched_sample"][0]["target_memory_type"] == "procedure"


def test_the_same_shape_passes_on_a_schema_without_relation_memory_revision(
    tmp_path: Path,
) -> None:
    """`relation_memory_revision` 只在真实 SDK schema 上存在, 缺列时判据照常成立。"""
    item = _run(tmp_path, [_relation()], with_relation_revision=False)
    assert item.verdict == a6.PASS, item.reason


# ------------------------------------------------------------------ (ii) 两端都是旧的
def test_both_endpoints_pre_existing_fails(tmp_path: Path) -> None:
    # 分支①的形状: 连两条已有记忆, 本 plan 只造了 relation memory, 没造节点。
    item = _run(tmp_path, [_relation(target=(OLD_FLOW, 1))])
    assert item.verdict == a6.FAIL
    assert item.numbers["relations_new_workflow_target_plus_relation_in_same_plan"] == 0


# ------------------------------------------------------------------ (iii) evolution 行
def test_an_evolution_row_without_a_relation_memory_fails(tmp_path: Path) -> None:
    # 血缘边(supersedes/amends/contests)没有 relation memory, relation_kind 也不是 applies_to。
    item = _run(tmp_path, [_relation(
        kind="supersedes", relation_memory_id=None, relation_memory_revision=None,
        source=(FLOW, 1), target=(FACT, 1))])
    assert item.verdict == a6.FAIL
    assert item.numbers["applies_to_rows"] == 0
    assert item.numbers["relation_kinds"] == ["supersedes"]


# ------------------------------------------------------------------ (iv) 悬空端点
def test_a_dangling_endpoint_revision_fails(tmp_path: Path) -> None:
    # target 指向 revision 2, 而 cognitive_memory_revisions 里只有 revision 1。
    item = _run(tmp_path, [_relation(target=(FLOW, 2))])
    assert item.verdict == a6.FAIL


# ------------------------------------------------------------------ (v) relation memory 不属于本 plan
def test_a_relation_memory_minted_by_another_plan_fails(tmp_path: Path) -> None:
    revisions = [row for row in _REVISIONS if row[0] != RELMEM] + [(RELMEM, 1) + OLD_PLAN]
    item = _run(tmp_path, [_relation()], revisions=revisions)
    assert item.verdict == a6.FAIL


def test_a_relation_memory_missing_from_heads_fails(tmp_path: Path) -> None:
    heads = [row for row in _HEADS if row[0] != RELMEM]
    item = _run(tmp_path, [_relation()], heads=heads)
    assert item.verdict == a6.FAIL


# ------------------------------------------------------------------ (vi) 只新建 source
def test_creating_only_the_source_claim_fails(tmp_path: Path) -> None:
    """形态 B 的半成品: 本轮新造一条同值 semantic 当 source, target 连的是旧流程。

    旧判据(「至少一个端点由本 plan 新建」)会给它 PASS —— 那正好是 DECISION-T-RELATION-FORM.md
    §1 判掉的第二个槽位, 也与改写后的 A6-6 计划行(target=本轮新建流程节点)相反。
    """
    item = _run(tmp_path, [_relation(source=(DUP, 1), target=(OLD_FLOW, 1))])
    assert item.verdict == a6.FAIL
    assert item.numbers["relations_new_workflow_target_plus_relation_in_same_plan"] == 0


def test_a_new_target_that_is_not_a_workflow_node_fails(tmp_path: Path) -> None:
    # 反向的形态 B: target 是本轮新建的, 但它是一条 semantic, 不是流程节点。
    item = _run(tmp_path, [_relation(source=(FACT, 1), target=(DUP, 1))])
    assert item.verdict == a6.FAIL


# ------------------------------------------------------------------ 无关系行
def test_no_relation_rows_stays_inconclusive(tmp_path: Path) -> None:
    item = _run(tmp_path, [])
    assert item.verdict == a6.INCONCLUSIVE
    assert "cognitive_relations 无行" in item.reason


def test_selftest_still_runs_every_item() -> None:
    assert a6.selftest() == 0
