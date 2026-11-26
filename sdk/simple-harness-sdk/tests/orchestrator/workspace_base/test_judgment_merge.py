# SPDX-License-Identifier: Apache-2.0
"""用真实底仓库、产物校验和判定副本验证多步骤交付；只有验收索引和事件接收端是替身。"""

import asyncio
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_orchestrator.artifacts.versioning import UpstreamInput
from agent_orchestrator.artifacts.workspace import WorkspaceManager
from agent_orchestrator.artifacts.workspace_base import git_blob_id
from agent_orchestrator.contracts import Artifact
from agent_orchestrator.orchestrator.commit_service_domains.results import ResultCommitsMixin
from agent_orchestrator.orchestrator.orchestrator_domains import attempt_judgment
from agent_orchestrator.orchestrator.orchestrator_domains.attempt_judgment import (
    AttemptJudgmentMixin,
)

BASE = b"first\na\nb\nmid\nc\nd\nlast\n"
LEFT = BASE.replace(b"first", b"FIRST")
RIGHT = BASE.replace(b"last", b"LAST")
BOTH = LEFT.replace(b"last", b"LAST")
PATH = "README.md"


def judge(tmp_path, monkeypatch, base, versions, *, inputs=(), linked=(), path=PATH, aliases=()):
    manager = WorkspaceManager(tmp_path / "workspaces")
    report = {}
    if base is not None:
        report["workspace_base"] = {"commit": manager.bases.commit_files(base)}
    mission = SimpleNamespace(id="mission", final_report=report)
    tasks, artifacts, ports, acceptances = [], {}, [], []
    for n, (name, data) in enumerate([*((path, data) for data in versions), *aliases]):
        key, task, attempt = f"a{n}", f"t{n}", f"attempt{n}"
        filename = tmp_path / key
        filename.write_bytes(data)
        artifacts[key] = Artifact(
            key,
            mission.id,
            task,
            attempt,
            "file",
            name,
            n + 1,
            sha256(data).hexdigest(),
            len(data),
            task,
            storage_uri=str(filename),
        )
        tasks.append(
            SimpleNamespace(
                id=task,
                accepted_artifacts=[key],
                accepted_result_id=None,
                status="COMPLETED",
                dependency_ids=[],
            )
        )
        acceptances.append(SimpleNamespace(acceptance_id=key, task_id=task))
        ports.append(
            dict(acceptance_id=key, producer_task_ref=task, artifact_id=key, output_port="out")
        )
    mode = SimpleNamespace(
        root_contributions=lambda _: {"root": list(artifacts)},
        semantics=lambda: SimpleNamespace(
            list_acceptances=lambda _: acceptances, list_acceptance_outputs=lambda _: ports
        ),
    )
    views = {}
    for child, parent in inputs:
        artifact = artifacts[f"a{parent}"]
        views[f"attempt{child}"] = [
            UpstreamInput(artifact.task_id, artifact.path, artifact.content_hash, artifact.id)
        ]
    commit = Mock()
    commit.record_artifact_merge_not_applicable = (
        ResultCommitsMixin.record_artifact_merge_not_applicable.__get__(commit)
    )
    commit.taskgraph_attempt_context.side_effect = lambda _, attempt: SimpleNamespace(
        upstream=views.get(attempt, ())
    )
    loop = SimpleNamespace(
        assembled=SimpleNamespace(workspaces=manager),
        store=SimpleNamespace(get_artifact=artifacts.__getitem__),
        commit=commit,
        _root_review=lambda *_: SimpleNamespace(
            carried_criteria=lambda _: [SimpleNamespace(task_id=tasks[n].id) for n in linked]
        ),
        _new_mode=lambda _: mode,
        _note=lambda _: None,
        _owner="owner",
        _register_copy=Mock(),
        _reimport_unsettled=lambda _: None,
        _current_source_files=lambda _: {},
        _assured_root_grades=lambda *_: {},
        _config=SimpleNamespace(deployment_policy=SimpleNamespace(denied_path_prefixes=[])),
    )
    for name in ("_judgment_tree", "_hierarchical_judgment_inputs"):
        setattr(loop, name, getattr(AttemptJudgmentMixin, name).__get__(loop))
    monkeypatch.setattr(attempt_judgment, "current_statements", lambda *_: [f"file:{path}"])
    judgments, _ = asyncio.run(AttemptJudgmentMixin._evaluate_criteria(loop, mission, tasks))
    assert judgments[0]["met"]
    tree = manager.root / "mission-judge-owner-verify"
    event = commit._emit.call_args.kwargs["payload"]
    change = AttemptJudgmentMixin._change_set(loop, mission, tasks)
    if base is None:
        assert change is None
        return tree, event, {}
    entries = {item["path"]: item for item in change["files"]}
    if base.get(path) == (tree / path).read_bytes() and path not in entries and not aliases:
        [record] = event["superseded"]
        assert change["unchanged_merge_facts"] == [
            {"path": path, "merge_conflict": record["merge_conflict"]}
        ]
        assert manager.bases.read(change["commit"], path) == base[path]
    for name, entry in entries.items():
        data = manager.bases.read(change["commit"], name)
        assert entry["blob"] == git_blob_id(data)
        assert entry["sha256"] == sha256(data).hexdigest()
        # 拼写别名在某些磁盘上会落在同一处；只在能一一对应时核对物化副本。
        if not aliases:
            assert (tree / name).read_bytes() == data
    return tree, event, entries


def test_clean_merge(tmp_path, monkeypatch):
    middle = BASE.replace(b"mid", b"MID")
    tree, event, entries = judge(
        tmp_path, monkeypatch, {PATH: BASE}, [LEFT, middle, RIGHT], linked=[0, 2]
    )
    assert (tree / PATH).read_bytes() == BOTH.replace(b"mid", b"MID")
    entry = entries[PATH]
    assert entry["merged_steps"] == ["t0", "t1", "t2"]
    assert entry["artifact_id"] is None and entry["task_id"] is None
    assert entry["overridden_steps"] == [] and not event["superseded"]
    assert event["base_merges"] == [{"path": PATH, "merged_steps": entry["merged_steps"]}]
    assert (tree / ".harness/accepted-outputs/t0/out" / PATH).read_bytes() == LEFT
    assert (tree / ".harness/accepted-outputs/t2/out" / PATH).read_bytes() == RIGHT


def test_conflict_uses_original_priority(tmp_path, monkeypatch):
    conflicting = BASE.replace(b"last", b"other")
    tree, event, entries = judge(
        tmp_path, monkeypatch, {PATH: BASE}, [LEFT, RIGHT, conflicting], linked=[0]
    )
    assert (tree / PATH).read_bytes() == LEFT
    assert entries[PATH]["artifact_id"] == "a0"
    assert entries[PATH]["merge_conflict"] == ["t1", "t2"]
    assert set(entries[PATH]["overridden_steps"]) == {"t1", "t2"}
    assert event["superseded"][0]["merge_conflict"] == entries[PATH]["merge_conflict"]
    assert all(b"<<<<<<<" not in p.read_bytes() for p in tree.rglob("*") if p.is_file())


def test_successor_rewrites_the_upstream_line(tmp_path, monkeypatch):
    next_version = LEFT.replace(b"FIRST", b"revised")
    tree, _, entries = judge(
        tmp_path, monkeypatch, {PATH: BASE}, [LEFT, next_version], inputs=[(1, 0)], linked=[0]
    )
    assert (tree / PATH).read_bytes() == next_version
    assert entries[PATH]["merged_steps"] == ["t0", "t1"]
    assert "merge_conflict" not in entries[PATH]


@pytest.mark.parametrize(
    ("base", "first", "second"),
    [
        ({PATH: b"\0" + BASE}, LEFT, RIGHT),
        ({PATH: b"\xff" + BASE}, LEFT, RIGHT),
        ({PATH: BASE}, b"\xff", RIGHT),
        ({}, LEFT, RIGHT),
    ],
)
def test_ineligible_inputs_keep_the_original_winner(tmp_path, monkeypatch, base, first, second):
    tree, _, entries = judge(tmp_path, monkeypatch, base, [first, second])
    assert (tree / PATH).read_bytes() == second
    assert entries[PATH]["artifact_id"] == "a1" and entries[PATH]["overridden_steps"] == ["t0"]
    assert {"merged_steps", "merge_conflict"}.isdisjoint(entries[PATH])


@pytest.mark.parametrize(
    ("path", "alias"),
    [
        ("Dir/é.md", "Dir/e\u0301.md"),
        ("Dir/é.md", "dir/other.md"),
        ("Dir/\u1fb3\u0323.md", "Dir/\u03b1\u0323\u0345.md"),
    ],
)
def test_ambiguous_spelling_is_reported(tmp_path, monkeypatch, path, alias):
    _, event, entries = judge(
        tmp_path, monkeypatch, {path: BASE}, [LEFT, RIGHT], path=path, aliases=[(alias, b"other")]
    )
    assert entries[path]["blob"] == git_blob_id(RIGHT)
    assert entries[path]["merge_skipped"] == "path_spelling"
    assert event["superseded"][0]["merge_skipped"] == "path_spelling"


def test_no_base_keeps_original_event_and_winner(tmp_path, monkeypatch):
    tree, event, _ = judge(tmp_path, monkeypatch, None, [LEFT, RIGHT])
    assert (tree / PATH).read_bytes() == RIGHT
    assert set(event) == {
        "reason",
        "subject",
        "redirect",
        "contributions",
        "artifacts",
        "superseded",
        "detail",
    }
    [record] = event["superseded"]
    assert set(record) == {
        "path",
        "kept_task_id",
        "kept_artifact_id",
        "kept_content_hash",
        "kept_by",
        "superseded_task_ids",
        "superseded",
    }
    assert record["kept_by"] == "acceptance_order" and record["superseded_task_ids"] == ["t0"]


def test_conflict_visible_when_the_winner_matches_the_base(tmp_path, monkeypatch):
    tree, event, entries = judge(
        tmp_path,
        monkeypatch,
        {PATH: BASE},
        [RIGHT, BASE.replace(b"last", b"other"), BASE],
        linked=[2],
    )
    assert (tree / PATH).read_bytes() == BASE and not entries
    assert event["superseded"][0]["merge_conflict"] == ["t0", "t1"]


def test_independent_edit_survives_upstream_rewrite(tmp_path, monkeypatch):
    successor = LEFT.replace(b"FIRST", b"new")
    tree, _, entries = judge(
        tmp_path, monkeypatch, {PATH: BASE}, [LEFT, RIGHT, successor], inputs=[(2, 0)]
    )
    assert (tree / PATH).read_bytes() == successor.replace(b"last", b"LAST")
    assert entries[PATH]["merged_steps"] == ["t0", "t1", "t2"]
