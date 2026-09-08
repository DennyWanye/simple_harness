# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""A6-9/A6-10 判据修正: 按契约复算普通投影, 而不是「争议期 edge 必须为 0」。

契约(memory-sdk `plans/2026-08-29-human-memory-digital-twin`, 只读引用):

* `slices/S3-cognitive-systems-recall.md` Task 6 —— twin graph 由
  「canonical active/contested/inferred records 和 relation rows」生成,
  `superseded/suppressed/expired` 才是被普通 view policy 挡掉的那三类。
  contested record 是展示素材。
* `acceptance.md` HM-S12 / HM-TO-A6 ——「relation/端点遗忘、争议或 ordinary
  projection policy 判定不可展示后 edge 退出」说的是 HM-S12 场景里那条由
  relation memory 承载的 knowledge 边(`applies_to`)。

SDK 0.6.31 实现与之一致: evolution 边(amends/supersedes/contests/…)只看
「两端 exact revision 是否都是可见 node」, 旧 revision 从不可见, 所以争议期唯一
能出现的 evolution 边就是 contests; knowledge 边另加
`twin_builder.twin_graph_record_is_active_visible`(head + active + uncontested +
不在冲突组 + 未抑制)。本测试把这两层口径钉死。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

_VERIFY = Path(__file__).resolve().parents[3] / "scripts" / "native" / "a6_verify.py"


def _load():
    spec = importlib.util.spec_from_file_location("a6_verify_under_test_9_10", _VERIFY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


a6 = _load()

_DDL = (
    "create table cognitive_memory_heads(memory_id text primary key,"
    " current_revision integer, memory_type text)",
    "create table cognitive_memory_revisions(memory_id text, revision integer,"
    " lifecycle_state text, conflict_status text, epistemic_status text,"
    " effective_privacy_class text, information_attributes_json text,"
    " content_json text, valid_from real, valid_to real, created_at real)",
    "create table cognitive_relations(relation_id text primary key,"
    " relation_domain text, relation_kind text, relation_memory_id text,"
    " relation_memory_revision integer, source_memory_id text,"
    " source_revision integer, target_memory_id text, target_revision integer)",
    "create table cognitive_evidence_spans(memory_id text, revision integer,"
    " evidence_id text, source_kind text)",
    "create table suppression_directives(directive_id text primary key,"
    " event_kind text, supersedes_directive_id text)",
    "create table suppression_targets(directive_id text, target_kind text,"
    " target_ref text)",
    "create table cognitive_conflict_groups(group_id text primary key,"
    " memory_id text, incumbent_revision integer, challenger_revision integer)",
    "create table cognitive_conflict_resolutions(group_id text primary key)",
)


def _claim(subject: str = "user:self") -> str:
    return json.dumps(
        {"memory_type": "semantic", "semantic_kind": "claim", "subject_entity": subject},
        ensure_ascii=False,
    )


_RELATION_CONTENT = json.dumps(
    {"memory_type": "semantic", "semantic_kind": "relation"}, ensure_ascii=False
)


def _db(
    tmp_path: Path,
    *,
    heads: Sequence[tuple[str, int, str]],
    revisions: Sequence[tuple[Any, ...]],
    relations: Sequence[tuple[Any, ...]] = (),
    spans: Sequence[tuple[str, int, str, str]] = (),
    directives: Sequence[tuple[str, str, Any]] = (),
    targets: Sequence[tuple[str, str, str]] = (),
    groups: Sequence[tuple[str, str, int, int]] = (),
    resolutions: Sequence[tuple[str]] = (),
    name: str = "human_memory_v7.db",
) -> "a6.RoDb":
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    conn = sqlite3.connect(path)
    for stmt in _DDL:
        conn.execute(stmt)
    conn.executemany("insert into cognitive_memory_heads values (?,?,?)", heads)
    conn.executemany(
        "insert into cognitive_memory_revisions values (?,?,?,?,?,?,?,?,?,?,?)", revisions
    )
    conn.executemany("insert into cognitive_relations values (?,?,?,?,?,?,?,?,?)", relations)
    conn.executemany("insert into cognitive_evidence_spans values (?,?,?,?)", spans)
    conn.executemany("insert into suppression_directives values (?,?,?)", directives)
    conn.executemany("insert into suppression_targets values (?,?,?)", targets)
    conn.executemany("insert into cognitive_conflict_groups values (?,?,?,?)", groups)
    conn.executemany("insert into cognitive_conflict_resolutions values (?)", resolutions)
    conn.commit()
    conn.close()
    return a6.RoDb(str(path), "human_memory_v7.db")


def _rev(
    memory_id: str,
    revision: int,
    *,
    lifecycle: str = "active",
    conflict: str = "uncontested",
    epistemic: str = "explicit_user",
    privacy: str = "personal",
    attrs: Sequence[str] = ("preference",),
    content: str | None = None,
    valid_from: float | None = None,
    valid_to: float | None = None,
    created_at: float = 100.0,
) -> tuple[Any, ...]:
    return (
        memory_id,
        revision,
        lifecycle,
        conflict,
        epistemic,
        privacy,
        json.dumps(list(attrs)),
        _claim() if content is None else content,
        valid_from,
        valid_to,
        created_at,
    )


def _progress(*notes: tuple[int, str], relations: int = 2) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [{"turn": 0, "outcome": "baseline", "cognitive_relations": 0}]
    for turn, note in notes:
        rows.append(
            {
                "turn": turn,
                "outcome": "manual_ui",
                "note": note,
                "cognitive_relations": relations,
            }
        )
    return rows


# --- run5 的真实形状: 一条记忆的 rev1/rev2/rev3, amends + contests 两条 evolution 边,
#     未裁决冲突组(incumbent=2, challenger=3=head), 外加一条被 UI 遗忘的记忆及其同源副本。
_M = "cognitive-memory-contested"
_F = "cognitive-memory-forgotten"
_A = "cognitive-memory-same-source-alias"
_K = "cognitive-memory-keeper"


def _run5_shape(tmp_path: Path, *, forget: bool = True) -> "a6.RoDb":
    return _db(
        tmp_path,
        heads=[
            (_M, 3, "semantic"),
            (_F, 1, "semantic"),
            (_A, 1, "episode"),
            (_K, 1, "semantic"),
        ],
        revisions=[
            _rev(_M, 1),
            _rev(_M, 2),
            _rev(_M, 3, conflict="contested"),
            _rev(_F, 1),
            _rev(_A, 1, content=json.dumps({"memory_type": "episode"})),
            _rev(_K, 1),
        ],
        relations=[
            ("rel-amends", "evolution", "amends", None, None, _M, 2, _M, 1),
            ("rel-contests", "evolution", "contests", None, None, _M, 3, _M, 2),
        ],
        spans=[
            (_M, 3, "ev-contested", "user_message"),
            (_F, 1, "ev-shared", "user_message"),
            (_A, 1, "ev-shared", "user_message"),
            (_K, 1, "ev-keeper", "user_message"),
        ],
        directives=[("dir-1", "directive", None)] if forget else [],
        targets=[("dir-1", "memory", _F)] if forget else [],
        groups=[("grp-1", _M, 2, 3)],
    )


def _a6_9(db, progress):
    return a6.item_a6_9(SimpleNamespace(hm=db, progress=progress))


def _a6_10(db, progress):
    return a6.item_a6_10(SimpleNamespace(hm=db, progress=progress))


# ---------------------------------------------------------------- A6-9


def test_contested_graph_keeps_the_contests_edge_and_hides_the_amends_lineage(tmp_path):
    """契约不是「争议期 edge 归零」: contests 边正是争议在普通图谱上的可读形式。"""
    db = _run5_shape(tmp_path)
    try:
        item = _a6_9(db, _progress((23, "graph=记忆关系图：3条记忆，1条关系。")))
        assert item.verdict == a6.PASS, item.reason
        # head: contested(rev3) + keeper + alias 已因同源抑制离图, forgotten 被直接抑制;
        # 冲突组额外带出 incumbent rev2。
        assert item.numbers["expected_nodes"] == 3
        assert item.numbers["expected_edges"] == 1
        assert [e["kind"] for e in item.numbers["visible_edges"]] == ["contests"]
        assert [e["kind"] for e in item.numbers["hidden_edges"]] == ["amends"]
        assert item.numbers["contested_pairs"] == [(_M[-12:], 2, 3)]
    finally:
        db.close()


def test_forgotten_memory_and_its_same_source_duplicate_leave_the_ordinary_graph(tmp_path):
    """SDK 2026-09-07 决定: 遗忘一条记忆同时抑制同一条 USER 证据下的同源副本。"""
    db_before = _run5_shape(tmp_path / "before", forget=False)
    db_after = _run5_shape(tmp_path / "after", forget=True)
    try:
        before = _a6_9(db_before, _progress((16, "nodes=5 edges=1")))
        after = _a6_9(db_after, _progress((23, "nodes=3 edges=1")))
        assert before.verdict == a6.PASS, before.reason
        assert before.numbers["expected_nodes"] == 5
        assert after.verdict == a6.PASS, after.reason
        assert after.numbers["expected_nodes"] == 3
        assert after.numbers["suppressed_direct"] == [_F[-12:]]
        assert after.numbers["suppressed_alias_same_source"] == [_A[-12:]]
    finally:
        db_before.close()
        db_after.close()


def test_observation_that_disagrees_with_the_recomputed_projection_fails(tmp_path):
    db = _run5_shape(tmp_path)
    try:
        item = _a6_9(db, _progress((23, "nodes=3 edges=2")))
        assert item.verdict == a6.FAIL
        assert "不符" in item.reason
    finally:
        db.close()


def test_missing_manual_ui_note_stays_inconclusive_but_still_reports_the_expectation(tmp_path):
    db = _run5_shape(tmp_path)
    try:
        item = _a6_9(db, [{"turn": 23, "outcome": "manual_ui", "note": "忘了记数字"}])
        assert item.verdict == a6.INCONCLUSIVE
        assert item.numbers["expected_nodes"] == 3
        assert item.numbers["expected_edges"] == 1
    finally:
        db.close()


def test_redacted_and_expired_records_never_enter_the_ordinary_graph(tmp_path):
    db = _db(
        tmp_path,
        heads=[("m-plain", 1, "semantic"), ("m-sensitive", 1, "semantic"),
               ("m-expired", 1, "semantic"), ("m-restricted", 1, "semantic")],
        revisions=[
            _rev("m-plain", 1),
            _rev("m-sensitive", 1, attrs=("health",)),
            _rev("m-expired", 1, valid_to=50.0),
            _rev("m-restricted", 1, privacy="restricted"),
        ],
        spans=[(m, 1, f"ev-{m}", "user_message")
               for m in ("m-plain", "m-sensitive", "m-expired", "m-restricted")],
    )
    try:
        item = _a6_9(db, _progress((16, "nodes=1 edges=0"), relations=0))
        assert item.verdict == a6.PASS, item.reason
        assert item.numbers["expected_nodes"] == 1
        assert item.numbers["redacted_excluded"] == [("m-sensitive"[-12:], 1)]
        assert item.numbers["expired_excluded"] == [("m-expired"[-12:], 1)]
    finally:
        db.close()


def test_knowledge_applies_to_edge_exits_while_an_endpoint_is_contested(tmp_path):
    """HM-S12 那条边: 端点进入争议 -> 边退出; 端点干净 -> 边出现。"""
    owner, source, target = "m-relation", "m-claim", "m-procedure"

    def build(conflict: str, groups):
        return _db(
            tmp_path / conflict,
            heads=[(owner, 1, "semantic"), (source, 1, "semantic"), (target, 1, "procedure")],
            revisions=[
                _rev(owner, 1, content=_RELATION_CONTENT),
                _rev(source, 1, conflict=conflict),
                _rev(target, 1, content=json.dumps({"memory_type": "procedure"})),
            ],
            relations=[
                ("rel-applies", "knowledge", "applies_to", owner, 1, source, 1, target, 1)
            ],
            spans=[(m, 1, f"ev-{m}", "user_message") for m in (owner, source, target)],
            groups=groups,
        )

    clean = build("uncontested", ())
    disputed = build("contested", ())
    try:
        # relation memory 自身从不作为节点, 所以 node 只有两个端点。
        ok = _a6_9(clean, _progress((16, "nodes=2 edges=1"), relations=1))
        assert ok.verdict == a6.PASS, ok.reason
        assert ok.numbers["relation_memory_nodes_excluded"] == [owner[-12:]]
        gone = _a6_9(disputed, _progress((21, "nodes=2 edges=0"), relations=1))
        assert gone.verdict == a6.PASS, gone.reason
        assert gone.numbers["expected_edges"] == 0
        assert [e["kind"] for e in gone.numbers["hidden_edges"]] == ["applies_to"]
    finally:
        clean.close()
        disputed.close()


def test_resolved_conflict_group_drops_the_incumbent_node_and_its_contests_edge(tmp_path):
    db = _db(
        tmp_path,
        heads=[(_M, 3, "semantic")],
        revisions=[_rev(_M, 1), _rev(_M, 2), _rev(_M, 3)],
        relations=[("rel-contests", "evolution", "contests", None, None, _M, 3, _M, 2)],
        spans=[(_M, 3, "ev", "user_message")],
        groups=[("grp-1", _M, 2, 3)],
        resolutions=[("grp-1",)],
    )
    try:
        item = _a6_9(db, _progress((24, "nodes=1 edges=0"), relations=1))
        assert item.verdict == a6.PASS, item.reason
        assert item.numbers["expected_nodes"] == 1
        assert item.numbers["expected_edges"] == 0
        assert item.numbers["contested_pairs"] == []
    finally:
        db.close()


def test_non_memory_suppression_scope_is_reported_as_uncomputable(tmp_path):
    db = _db(
        tmp_path / "subject",
        heads=[(_K, 1, "semantic")],
        revisions=[_rev(_K, 1)],
        spans=[(_K, 1, "ev", "user_message")],
        directives=[("dir-1", "directive", None)],
        targets=[("dir-1", "subject", "user:self")],
    )
    try:
        item = _a6_9(db, _progress((23, "nodes=1 edges=0"), relations=0))
        assert item.verdict == a6.INCONCLUSIVE
        assert "非 memory 作用域" in item.reason
    finally:
        db.close()


def test_revoked_directive_stops_hiding_its_target(tmp_path):
    db = _db(
        tmp_path,
        heads=[(_F, 1, "semantic"), (_K, 1, "semantic")],
        revisions=[_rev(_F, 1), _rev(_K, 1)],
        spans=[(_F, 1, "ev-f", "user_message"), (_K, 1, "ev-k", "user_message")],
        directives=[("dir-1", "directive", None), ("dir-2", "revoke", "dir-1")],
        targets=[("dir-1", "memory", _F)],
    )
    try:
        item = _a6_9(db, _progress((23, "nodes=2 edges=0"), relations=0))
        assert item.verdict == a6.PASS, item.reason
        assert item.numbers["suppressed_direct"] == []
    finally:
        db.close()


def test_both_manual_ui_note_dialects_are_parsed():
    progress = _progress(
        (16, "nodes=10 edges=0 inv_before=83 inv_after=83"),
        (23, "T23 UI forget: suppression_directives=1 graph=记忆关系图：13条记忆，1条关系。 inv_delta=0"),
    )
    observed = a6._manual_ui_observations(SimpleNamespace(progress=progress))
    assert [(o["turn"], o["nodes"], o["edges"]) for o in observed] == [
        (16, 10, 0),
        (23, 13, 1),
    ]


# ---------------------------------------------------------------- A6-10


def test_forget_then_reopen_with_identical_counts_passes(tmp_path):
    db = _run5_shape(tmp_path)
    try:
        item = _a6_10(
            db,
            _progress(
                (23, "T23 UI forget: graph=记忆关系图：3条记忆，1条关系。"),
                (24, "close_reopen: 记忆关系图：3条记忆，1条关系。"),
            ),
        )
        assert item.verdict == a6.PASS, item.reason
        assert item.numbers["nodes_removed_by_suppression"] == 2
        assert item.numbers["knowledge_relation_rows"] == 0
        assert "由 A6-6 承载" in item.reason
    finally:
        db.close()


def test_reopen_that_revives_an_edge_fails(tmp_path):
    db = _run5_shape(tmp_path)
    try:
        item = _a6_10(
            db,
            _progress(
                (23, "nodes=3 edges=1"),
                (24, "nodes=4 edges=2"),
            ),
        )
        assert item.verdict == a6.FAIL
        assert "复活" in item.reason
    finally:
        db.close()


def test_forget_observation_that_still_shows_the_forgotten_node_fails(tmp_path):
    db = _run5_shape(tmp_path)
    try:
        item = _a6_10(db, _progress((23, "nodes=5 edges=1"), (24, "nodes=5 edges=1")))
        assert item.verdict == a6.FAIL
        assert "没有按 ordinary policy 离图" in item.reason
    finally:
        db.close()


def test_directive_that_removes_nothing_fails(tmp_path):
    db = _db(
        tmp_path,
        heads=[(_K, 1, "semantic"), (_M, 3, "semantic")],
        revisions=[_rev(_K, 1), _rev(_M, 3)],
        relations=[("rel-relates", "evolution", "relates_to", None, None, _K, 1, _M, 3)],
        spans=[(_K, 1, "ev", "user_message"), (_M, 3, "ev-m", "user_message")],
        directives=[("dir-1", "directive", None)],
        targets=[("dir-1", "memory", "cognitive-memory-not-here")],
    )
    try:
        item = _a6_10(
            db, _progress((23, "nodes=2 edges=1"), (24, "nodes=2 edges=1"), relations=1)
        )
        assert item.verdict == a6.FAIL
        assert "遗忘对普通图谱无效" in item.reason
    finally:
        db.close()


def test_relations_physically_deleted_still_fails_first(tmp_path):
    db = _run5_shape(tmp_path)
    progress = _progress((23, "nodes=3 edges=1"), (24, "nodes=3 edges=1"))
    progress[0]["cognitive_relations"] = 9
    try:
        item = _a6_10(db, progress)
        assert item.verdict == a6.FAIL
        assert "append-only" in item.reason
    finally:
        db.close()


def test_single_post_forget_observation_stays_inconclusive(tmp_path):
    db = _run5_shape(tmp_path)
    try:
        item = _a6_10(db, _progress((23, "nodes=3 edges=1")))
        assert item.verdict == a6.INCONCLUSIVE
        assert "不足两条" in item.reason
    finally:
        db.close()


def test_selftest_still_runs_every_item() -> None:
    assert a6.selftest() == 0
